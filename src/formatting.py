"""Składanie dokumentacji do postaci tekstu i pomocnicze nazwy wizyt.

Lekarz poprawia całą dokumentację jako JEDEN tekst — notatkę z wizyty, pisaną ciągłym
tekstem tak, jak pisze ją lekarz. Ten moduł zamienia odpowiedź modelu (`DokumentacjaDraft`)
w ten tekst.

Szkice zapisane wcześniej, w formacie sekcji A–Q (każdy obszar w osobnym polu), składamy
po staremu — z nagłówkami i znacznikiem „nie poruszono" — żeby historia dalej je pokazywała.
"""

from typing import TYPE_CHECKING, Any

from .schemas import SEKCJE

if TYPE_CHECKING:  # pandas jest potrzebny wyłącznie dla tabeli Streamlita (patrz niżej)
    import pandas as pd


NIE_PORUSZONO = "Nie poruszono podczas konsultacji."

# Sformułowanie z promptu klinicznego, sekcja J: nie „bez myśli samobójczych", tylko
# informacja, że obszaru nie oceniano. To rozróżnienie jest powodem istnienia trzeciego stanu.
J_NIE_OCENIANO = (
    "Nie odnotowano danych pozwalających ocenić ten obszar — temat nie został poruszony "
    "podczas konsultacji."
)

RYZYKO_OPIS = {
    "OBECNE": "zgłaszane przez pacjenta",
    "ZAPRZECZA": "pacjent zaprzecza",
    "NIE_OCENIANO": "nie oceniano podczas konsultacji",
}

PILNE = "!! FRAGMENT DO PILNEJ WERYFIKACJI PRZEZ LEKARZA"


def enum_value(value: Any) -> str:
    """Wartość enuma jako tekst.

    `str()` na elemencie `(str, Enum)` daje „Klasa.NAZWA", a nie samą wartość —
    a `model_dump()` zwraca właśnie elementy enuma. Bez tego porównania po tekście
    cicho nie trafiają i wpadamy w gałąź zapasową.
    """
    return str(getattr(value, "value", value) or "")


def visit_type_label(visit_type: str | None) -> str:
    v = (visit_type or "").strip().lower()
    if v.startswith("pierw"):
        return "Pierwsza wizyta"
    if v.startswith("kolej"):
        return "Wizyta kolejna"
    return "Typ wizyty nieokreślony"


def display_name(visit: dict[str, Any]) -> str:
    """Czytelna nazwa wizyty: 'Wizyta #N z dnia RRRR-MM-DD — etykieta'."""
    name = f"Wizyta #{visit.get('id', '?')}"
    created = (visit.get("created_at") or "")[:10]
    if created:
        name += f" z dnia {created}"
    label = (visit.get("visit_label") or "").strip()
    if label:
        name += f" — {label}"
    return name


def audio_unusable(note: dict[str, Any]) -> bool:
    """Czy model zgłosił, że w nagraniu nie było zrozumiałej mowy."""
    return enum_value(note.get("jakosc_nagrania")).upper() == "BRAK_MOWY"


def audio_quality_label(note: dict[str, Any]) -> str | None:
    quality = enum_value(note.get("jakosc_nagrania")).upper()
    if quality == "BRAK_MOWY":
        return "W nagraniu nie wykryto zrozumiałej mowy — dokumentacja jest pusta albo niepełna."
    if quality == "SLABA":
        return "Nagranie częściowo niezrozumiałe — sprawdź dokumentację szczególnie uważnie."
    return None


def _safety(draft: dict[str, Any]) -> dict[str, Any]:
    """Pole bezpieczeństwa — `bezpieczenstwo` w notatce, `j_bezpieczenstwo` w starych szkicach."""
    return draft.get("bezpieczenstwo") or draft.get("j_bezpieczenstwo") or {}


def risk_status(draft: dict[str, Any]) -> str:
    """Status ryzyka; brak danych traktujemy jako NIE_OCENIANO, nigdy jako zaprzeczenie."""
    status = enum_value(_safety(draft).get("status")).upper()
    return status if status in RYZYKO_OPIS else "NIE_OCENIANO"


def risk_is_urgent(draft: dict[str, Any]) -> bool:
    return bool(_safety(draft).get("pilne"))


def is_note_draft(draft: dict[str, Any] | None) -> bool:
    """Czy to szkic, który umiemy złożyć w dokument — notatka albo stary format A–Q."""
    return bool(draft) and ("notatka" in draft or "j_bezpieczenstwo" in draft)


def _section_body(field: str, draft: dict[str, Any]) -> str:
    if field == "e_leki":
        leki = [lek for lek in (draft.get("e_leki") or []) if (lek.get("nazwa") or "").strip()]
        if not leki:
            return NIE_PORUSZONO
        parts = []
        for i, lek in enumerate(leki, 1):
            opis = (lek.get("opis") or "").strip()
            parts.append(f"{i}. {lek['nazwa'].strip()}" + (f"\n{opis}" if opis else ""))
        return "\n\n".join(parts)

    if field == "j_bezpieczenstwo":
        j = draft.get("j_bezpieczenstwo") or {}
        status = risk_status(draft)
        opis = (j.get("opis") or "").strip()
        if status == "NIE_OCENIANO" and not opis:
            body = J_NIE_OCENIANO
        else:
            body = f"Myśli i zachowania samobójcze: {RYZYKO_OPIS[status]}."
            if opis:
                body += f"\n{opis}"
        if risk_is_urgent(draft):
            body = f"{PILNE}\n{body}"
        return body

    return (draft.get(field) or "").strip() or NIE_PORUSZONO


def draft_to_document(draft: dict[str, Any]) -> str:
    """Odpowiedź modelu → jeden tekst do edycji przez lekarza.

    Lista `do_weryfikacji` NIE trafia do dokumentu. To lista zadań dla lekarza przed
    zatwierdzeniem, a nie treść dokumentacji medycznej.

    Ostrzeżenie o słabym nagraniu natomiast trafia — i to na sam początek. Notatka
    z częściowo niezrozumiałego nagrania czyta się jak pełna, a jest dziurawa; lekarz
    musi to zobaczyć, zanim ją wklei.
    """
    if "notatka" not in draft:
        return _legacy_document(draft)
    blocks = [str(draft.get("notatka") or "").strip()]
    if warning := audio_quality_label(draft):
        blocks.insert(0, f"!! UWAGA: {warning}")
    return "\n\n".join(b for b in blocks if b).strip() + "\n"


def _legacy_document(draft: dict[str, Any]) -> str:
    """Szkic w starym formacie sekcji A–Q — z nagłówkami i oznaczeniem nieporuszonych."""
    blocks = [f"{title}\n{_section_body(field, draft)}" for field, title in SEKCJE]
    if warning := audio_quality_label(draft):
        blocks.insert(0, f"!! UWAGA: {warning}")
    return "\n\n".join(blocks).strip() + "\n"


def with_header(
    document: str,
    *,
    title: str | None = None,
    visit_type: str | None = None,
    created_at: str | None = None,
    doctor_name: str | None = None,
) -> str:
    """Dokument z nagłówkiem wizyty — to trafia do schowka.

    Nagłówek dokładamy przy kopiowaniu, a nie do pola edycji: data i prowadzący pochodzą
    z rekordu wizyty i nie powinny dać się przypadkiem nadpisać przy poprawianiu treści.
    """
    lines: list[str] = []
    if title:
        lines.append(title)
    meta = []
    if visit_type:
        meta.append(visit_type_label(visit_type))
    if created_at:
        meta.append(f"Data: {created_at[:10]}")
    if doctor_name:
        meta.append(f"Prowadzący: {doctor_name}")
    if meta:
        lines.append(" · ".join(meta))
    if lines:
        lines.append("=" * 48)
        lines.append("")
    return "\n".join(lines) + (document or "").strip() + "\n"


# Polskie nazwy kolumn dla widoku Historii. Kolejność = porządek kluczy w słowniku.
# Świadomie BEZ tokenów i kosztu — lekarz ich nie widzi, są tylko w panelu właściciela.
_COLUMN_RENAME: dict[str, str] = {
    "id": "numer",
    "visit_label": "nazwa wizyty",
    "created_at": "utworzona",
    "visit_type": "pierwsza czy kolejna wizyta",
    "doctor_name": "prowadzący",
    "status": "status",
    "pipeline": "tryb",
}


def humanize_visits_df(visits: list[dict[str, Any]]) -> "pd.DataFrame":
    """Tabela wizyt z polskimi nagłówkami, w ustalonej kolejności.

    Jedyna funkcja w tym module wymagająca pandas — importujemy go tutaj, żeby
    reszta działała także w backendzie bez pandas.
    """
    import pandas as pd

    if not visits:
        return pd.DataFrame(columns=list(_COLUMN_RENAME.values()))

    df = pd.DataFrame(visits)
    if "created_at" in df.columns:
        df["created_at"] = df["created_at"].astype(str).str.replace("T", " ").str[:16]
    if "visit_type" in df.columns:
        df["visit_type"] = df["visit_type"].apply(visit_type_label)

    keep = [c for c in _COLUMN_RENAME if c in df.columns]
    return df[keep].rename(columns=_COLUMN_RENAME)
