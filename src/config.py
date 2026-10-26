import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = DATA_DIR / "mindscribe.db"

AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _from_secrets(key: str):
    """Fallback to Streamlit secrets when env var is not set (used on Streamlit Cloud)."""
    try:
        import streamlit as st

        return st.secrets.get(key)
    except Exception:
        return None


def _as_bool(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(v, default: int) -> int:
    """Liczba z env/sekretów, z wartością zapasową przy braku albo śmieciu.

    Literówka w sekrecie nie może wywalić startu aplikacji — lepiej wejść
    z domyślną wartością niż nie wejść wcale.
    """
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _database_url() -> str:
    """Adres bazy. Bez konfiguracji — lokalny SQLite, żeby dev i testy działały bez serwera.

    Neon podaje adres zaczynający się od `postgresql://`; SQLAlchemy potrzebuje jawnego
    sterownika, więc dopisujemy `+psycopg`. Dzięki temu można wkleić string prosto
    z panelu Neona bez ręcznych poprawek.
    """
    url = (os.getenv("DATABASE_URL") or _from_secrets("DATABASE_URL") or "").strip()
    if not url:
        return f"sqlite:///{DB_PATH}"
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):  # starszy wariant, spotykany u niektórych dostawców
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _database_url()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or _from_secrets("GEMINI_API_KEY") or ""
# Rodzinę 2.5 Google wyłączył na Vertex AI 20.10.2026 — zapytanie o nią kończy się 404.
# Domyślny jest więc Gemini 3.8 Flash, który Google wskazuje jako następcę i 2.5 Pro,
# i 2.5 Flash.
DEFAULT_MODEL = "gemini-3.8-flash"
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or _from_secrets("GEMINI_MODEL") or DEFAULT_MODEL

# Budżet tokenów „myślenia" — tylko dla modeli 2.x; od Gemini 3 myślenie ustawia się
# poziomem (`GEMINI_THINKING_LEVEL`, niżej). 8192 to domyślna wartość Google dla 2.5 Pro — schodzenie
# poniżej niej oszczędza grosze i psuje to, po co ten model tu jest. Wcześniejsze 256
# było wartością z czasów Flasha i przy długiej wizycie okazało się za małe: model
# mylił objawy aktualne z tymi, które pacjent zanegował.
#
# Uwaga na koszt: te tokeny są płatne jak wyjście, przy Pro $10/1M — to one, a nie
# audio, odpowiadają za wzrost rachunku. `-1` znaczy dynamiczny (model decyduje sam),
# ale wtedy koszt wizyty przestaje być przewidywalny.
GEMINI_THINKING_BUDGET = _as_int(
    os.getenv("GEMINI_THINKING_BUDGET") or _from_secrets("GEMINI_THINKING_BUDGET"), 8192
)

# Poziom myślenia modeli Gemini 3+: minimal, low, medium albo high. Te modele nie
# przyjmują już budżetu w tokenach. `medium` to domyślna wartość Google dla Flasha —
# kompromis między uważnym czytaniem długiej wizyty a czasem, na który czeka lekarz.
_THINKING_LEVELS = ("minimal", "low", "medium", "high")
_level = str(os.getenv("GEMINI_THINKING_LEVEL") or _from_secrets("GEMINI_THINKING_LEVEL") or "")
GEMINI_THINKING_LEVEL = _level.strip().lower() if _level.strip().lower() in _THINKING_LEVELS else "medium"


def uses_thinking_level(model: str) -> bool:
    """Czy model steruje myśleniem poziomem (Gemini 3+), a nie budżetem tokenów (2.x).

    Rozpoznajemy po numerze wersji w nazwie. Pomyłka w drugą stronę kończy się
    błędem 400 dopiero po wysłaniu całego nagrania, więc nieznany format nazwy
    traktujemy jak nowy model — stare 2.x mają nazwy, które znamy.
    """
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    if not name.startswith("gemini-"):
        return True
    version = name[len("gemini-"):].split("-", 1)[0]
    try:
        return float(version) >= 3
    except ValueError:
        return True


# Modele, które nie potrafią wyłączyć myślenia — minimalny dopuszczalny budżet.
# `gemini-2.5-pro` odrzuca 0, a poniżej 128 zwraca 400.
_MIN_THINKING_BUDGET = {"gemini-2.5-pro": 128}


def thinking_budget_for(model: str, budget: int) -> int:
    """Przytnij budżet myślenia do wartości, którą ten model przyjmie.

    Przycinamy, zamiast pozwolić API zwrócić 400. Twardy błąd wypadłby dopiero po
    wysłaniu 45-minutowego audio — czyli po opłaceniu jego przetworzenia i w środku
    prawdziwej wizyty.
    """
    if budget == -1:  # dynamiczny — model decyduje sam, zawsze dopuszczalny
        return budget
    return max(budget, _MIN_THINKING_BUDGET.get((model or "").strip().lower(), 0))


def _bool_setting(name: str, default: bool) -> bool:
    """Przełącznik z env albo sekretów, z wartością domyślną tylko przy BRAKU wpisu.

    Nie przez `or`: `False` z TOML-a jest dla `or` tak samo „puste" jak brak wpisu,
    więc przy domyślnie włączonym przełączniku `= false` byłoby po cichu ignorowane.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        raw = _from_secrets(name)
    if raw is None or raw == "":
        return default
    return _as_bool(raw)


# Transkrypcja osobnym, szybszym modelem, równolegle z dokumentacją. Dosłowna
# transkrypcja to największa część tekstu, który model pisze token po tokenie —
# a przy jednym wywołaniu pisał ją ten sam, wolniejszy model, przed dokumentacją.
# Teraz czas czekania to dłuższe z dwóch wywołań, nie ich suma.
SEPARATE_TRANSCRIPTION = _bool_setting("SEPARATE_TRANSCRIPTION", True)
TRANSCRIPTION_MODEL = (
    os.getenv("TRANSCRIPTION_MODEL") or _from_secrets("TRANSCRIPTION_MODEL") or DEFAULT_MODEL
)


USE_VERTEX_AI = _as_bool(os.getenv("USE_VERTEX_AI") or _from_secrets("USE_VERTEX_AI") or "false")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID") or _from_secrets("GCP_PROJECT_ID") or ""
# `eu` to wieloregionalny adres Unii: przetwarzanie w krajach UE, bez wskazania jednego
# regionu. Nowe modele trafiają tam wcześniej niż do pojedynczych regionów —
# `gemini-3.8-flash` w `europe-west4` zwracał 404. `global` też działa, ale wtedy
# nagranie może być przetwarzane poza UE.
GCP_LOCATION = os.getenv("GCP_LOCATION") or _from_secrets("GCP_LOCATION") or "eu"


def _tolerant_json_loads(raw: str) -> dict:
    """json.loads, ale z auto-naprawą literalnych nowych linii w 'private_key'.

    Najczęstszy problem przy wklejaniu klucza service account do TOML:
    wartość private_key zawiera literalne \\n, które po wklejeniu w '''...'''
    stają się prawdziwymi nowymi liniami — JSON tego nie znosi w stringu.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    m = re.search(r'"private_key"\s*:\s*"', raw)
    if not m:
        return json.loads(raw)  # rzuci oryginalny błąd

    start = m.end()
    i = start
    while i < len(raw):
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == '"':
            break
        i += 1
    if i >= len(raw):
        return json.loads(raw)

    value = raw[start:i]
    fixed_value = value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return json.loads(raw[:start] + fixed_value + raw[i:])


def _gcp_credentials_info() -> dict | None:
    """Zbierz dict z poświadczeniami service account z dowolnego z obsługiwanych źródeł.

    Kolejność:
      1. GOOGLE_APPLICATION_CREDENTIALS_JSON (string z całym JSON, env lub secrets) — tolerancyjny parser.
      2. Sekcja [gcp_service_account] w secrets (zalecany styl Streamlit dla GCP).
    """
    raw = (
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        or _from_secrets("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        or ""
    ).strip()
    if raw:
        return _tolerant_json_loads(raw)

    section = _from_secrets("gcp_service_account")
    if section:
        return dict(section)

    return None


# Ile zatwierdzonych notatek doklejamy jako wzorzec stylu. Do promptu trafiają razem
# z transkrypcjami, więc gdyby model zaczął przenosić z nich treść do nowej notatki,
# `FEW_SHOT_LIMIT=0` wyłącza je bez zmiany kodu.
FEW_SHOT_LIMIT = _as_int(os.getenv("FEW_SHOT_LIMIT") or _from_secrets("FEW_SHOT_LIMIT"), 3)
