"""Wycena zużycia Gemini — per model i per próg kontekstu.

Stawki USD za 1M tokenów. Sprawdź i zaktualizuj na
https://ai.google.dev/gemini-api/docs/pricing — cała tabela do poprawienia siedzi
w jednym bloku niżej, nic poza nim nie zawiera liczb z cennika.

Dlaczego per model: wcześniej stawki były zaszyte na sztywno dla Gemini 2.5 Flash.
Zmiana `GEMINI_MODEL` na Pro dawała koszt policzony po cenach Flasha — **ponad
dwukrotnie zaniżony, bez żadnego sygnału**. Panel właściciela pokazywałby wtedy
spadek kosztów po przejściu na droższy model, co czyta się jak dobra wiadomość.

Tokeny „myślenia" są rozliczane jak wyjście — przy Pro to główny składnik rachunku.
Tokenów z cache (`cached_content_token_count`) ten pipeline nie używa i ich nie liczymy.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rates:
    """Stawki USD za 1M tokenów w jednej taryfie."""

    text_input: float
    audio_input: float
    output: float  # obejmuje tokeny „myślenia"


@dataclass(frozen=True)
class ModelPricing:
    standard: Rates
    # Modele Pro mają wyższą taryfę powyżej progu tokenów promptu. 200 tys. tokenów
    # to ~1 h 44 min nagrania, więc przy długiej wizycie to realny scenariusz.
    long_context: Rates | None = None
    long_context_threshold: int | None = None


# --- Jedyne miejsce z liczbami z cennika ---------------------------------------

PRICES_CHECKED_ON = "2026-10-26"
# Ceny pochodzą ze źródeł wtórnych — oficjalne domeny Google były niedostępne przy
# ich ustalaniu. Potwierdź w konsoli Google Cloud, zanim oprzesz na nich wycenę usługi.
PRICES_ARE_PROVISIONAL = True

MODEL_PRICING_USD_PER_1M: dict[str, ModelPricing] = {
    # Stawki katalogowe. Do 31.12.2026 Google daje na 3.8 Flash cenę promocyjną
    # o połowę niższą (0,75 / 3,75) — liczymy katalogową, żeby szacunek nie zaniżał
    # kosztów od stycznia. Adres `eu` bywa o ~10% droższy. Audio jak tekst.
    "gemini-3.8-flash": ModelPricing(standard=Rates(1.50, 1.50, 7.50)),
    "gemini-2.5-flash": ModelPricing(standard=Rates(0.30, 1.00, 2.50)),
    "gemini-2.5-pro": ModelPricing(
        standard=Rates(1.25, 1.25, 10.00),
        long_context=Rates(2.50, 2.50, 15.00),
        long_context_threshold=200_000,
    ),
}

# Wydania datowane i preview mają cennik rodziny — wpisujemy je jawnie, bo
# dopasowanie po prefiksie byłoby zgadywaniem (patrz `_resolve_model`).
MODEL_ALIASES: dict[str, str] = {
    "gemini-2.5-pro-preview-06-05": "gemini-2.5-pro",
    "gemini-2.5-flash-preview-05-20": "gemini-2.5-flash",
}

# Modele, o których wiemy, że istnieją, ale nie mamy potwierdzonych stawek.
# Mają świadomie iść ścieżką „model nieznany", a nie udawać znany cennik.
UNPRICED_MODELS: frozenset[str] = frozenset(
    {"gemini-2.5-flash-lite", "gemini-3-flash", "gemini-3-pro"}
)


def usd_to_pln(usd: float, rate: float) -> float:
    return float(usd) * float(rate)


# Gemini tokenizuje audio ze stałą częstotliwością — stąd da się z tokenów odtworzyć
# przybliżoną długość nagrania.
AUDIO_TOKENS_PER_SECOND = 32

# Poniżej tego progu szacunek jest bezwartościowy: narzut tekstowy promptu (system
# prompt, schemat, few-shot) to rząd 1-3 tys. tokenów, więc przy krótkim nagraniu
# dominuje wynik. Przy realnej wizycie stanowi kilka procent i jest do przyjęcia.
_MIN_TRUSTWORTHY_SECONDS = 300


def estimate_audio_seconds(
    audio_tokens: int | None,
    *,
    modality_known: bool = True,
    tokens_per_second: int = AUDIO_TOKENS_PER_SECOND,
) -> float | None:
    """Przybliżona długość nagrania z liczby tokenów audio.

    Zwraca `None`, gdy szacunek byłby niewiarygodny — brak danych, brak rozbicia
    modalności (wtedy liczba zawiera też tekst promptu) albo wynik poniżej progu.
    Wołający ma pokazać „—", a nie zmyśloną wartość.
    """
    if not audio_tokens or audio_tokens <= 0 or not modality_known:
        return None
    seconds = float(audio_tokens) / float(tokens_per_second)
    if seconds < _MIN_TRUSTWORTHY_SECONDS:
        return None
    return round(seconds, 1)


def format_duration(seconds: float | None) -> str:
    """Sekundy → „1 h 23 min" / „14 min" / „—"."""
    if not seconds or seconds <= 0:
        return "—"
    total_minutes = int(round(seconds / 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def _modality_breakdown(prompt_details) -> dict[str, int]:
    """Convert SDK's prompt_tokens_details (list of ModalityTokenCount) -> {modality: count}."""
    out: dict[str, int] = {}
    if not prompt_details:
        return out
    for item in prompt_details:
        if isinstance(item, dict):
            modality = item.get("modality")
            count = item.get("token_count")
        else:
            modality = getattr(item, "modality", None)
            count = getattr(item, "token_count", None)
        if modality is None:
            continue
        key = str(modality).lower().split(".")[-1]
        out[key] = out.get(key, 0) + int(count or 0)
    return out


def normalize_model_id(model: str | None) -> str:
    """„publishers/google/models/gemini-2.5-pro@001" → „gemini-2.5-pro"."""
    name = (model or "").strip().lower()
    for prefix in ("publishers/google/models/", "models/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name.split("@", 1)[0]


def _fallback_rates() -> Rates:
    """Najdroższa znana stawka w każdej pozycji z osobna.

    Liczona z tabeli, nie wpisana na sztywno: dodanie droższego modelu automatycznie
    podnosi podłogę, więc stawka zapasowa nie może się rozjechać z rzeczywistością.
    """
    tiers = [
        tier
        for pricing in MODEL_PRICING_USD_PER_1M.values()
        for tier in (pricing.standard, pricing.long_context)
        if tier is not None
    ]
    return Rates(
        text_input=max(t.text_input for t in tiers),
        audio_input=max(t.audio_input for t in tiers),
        output=max(t.output for t in tiers),
    )


def is_priced(model: str | None) -> bool:
    """Czy dla tego modelu mamy potwierdzone stawki."""
    name = normalize_model_id(model)
    name = MODEL_ALIASES.get(name, name)
    return name not in UNPRICED_MODELS and name in MODEL_PRICING_USD_PER_1M


def resolve_rates(model: str | None, prompt_tokens: int) -> tuple[Rates, str, bool]:
    """Stawki dla modelu i wielkości promptu → (stawki, nazwa taryfy, czy znane).

    Dopasowanie jest dokładne (po normalizacji i aliasach), NIE prefiksowe. Prefiks
    wyceniłby `gemini-2.5-flash-lite` po pełnych stawkach Flasha i sprawił, że
    nieznana rodzina wyglądałaby na znaną — dokładnie ta cicha nieprawda, którą
    ten moduł ma likwidować.
    """
    name = normalize_model_id(model)
    name = MODEL_ALIASES.get(name, name)
    pricing = None if name in UNPRICED_MODELS else MODEL_PRICING_USD_PER_1M.get(name)
    if pricing is None:
        return _fallback_rates(), "fallback", False

    threshold = pricing.long_context_threshold
    if pricing.long_context is not None and threshold is not None and prompt_tokens > threshold:
        return pricing.long_context, "long_context", True
    return pricing.standard, "standard", True


def estimate_usage_and_cost(usage_metadata, *, model: str) -> dict:
    """Liczniki tokenów i szacunek kosztu w USD dla jednego wywołania.

    `model` jest **wymagany i bez wartości domyślnej**. Domyślna kazałaby temu
    modułowi importować konfigurację i przywróciłaby cichy default, przez który
    koszty potrafiły być liczone po cenniku innego modelu; brak domyślnej sprawia,
    że wywołanie bez modelu wywala się głośno, w testach.

    Gdy SDK nie poda rozbicia modalności, cały prompt liczymy po stawce audio —
    to konserwatywne oszacowanie dla tego pipeline'u, w którym audio dominuje.
    """
    rates, tier, pricing_known = resolve_rates(
        model, int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
    )

    if usage_metadata is None:
        return {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
            "prompt_audio_tokens": 0,
            "prompt_text_tokens": 0,
            "modality_known": False,
            "estimated_cost_usd": 0.0,
            "model": normalize_model_id(model),
            "pricing_known": pricing_known,
            "pricing_tier": tier,
        }

    prompt_total = int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
    output_total = int(getattr(usage_metadata, "candidates_token_count", 0) or 0)
    # Tokeny "myślenia" (Gemini 2.5). Bywają raportowane osobno i też są płatne jak output.
    thoughts_total = int(getattr(usage_metadata, "thoughts_token_count", 0) or 0)
    grand_total = int(
        getattr(usage_metadata, "total_token_count", 0)
        or (prompt_total + output_total + thoughts_total)
    )

    details = _modality_breakdown(getattr(usage_metadata, "prompt_tokens_details", None))
    audio_tokens = details.get("audio", 0)
    text_tokens = details.get("text", 0) + details.get("image", 0) + details.get("video", 0)

    modality_known = bool(audio_tokens or text_tokens)
    if not modality_known:
        audio_tokens = prompt_total
        text_tokens = 0

    # Taryfa wybrana rozmiarem promptu wycenia CAŁE żądanie, także wyjście:
    # krótka odpowiedź na prompt 250-tysięczny idzie po stawce długiego kontekstu.
    billable_output = output_total + thoughts_total
    cost = (
        audio_tokens * rates.audio_input
        + text_tokens * rates.text_input
        + billable_output * rates.output
    ) / 1_000_000

    return {
        "prompt_tokens": prompt_total,
        "output_tokens": output_total,
        "thoughts_tokens": thoughts_total,
        "total_tokens": grand_total,
        "prompt_audio_tokens": audio_tokens,
        "prompt_text_tokens": text_tokens,
        "modality_known": modality_known,
        "estimated_cost_usd": round(cost, 6),
        "model": normalize_model_id(model),
        "pricing_known": pricing_known,
        "pricing_tier": tier,
    }


def merge_usage(primary: dict, secondary: dict | None) -> dict:
    """Zużycie dwóch wywołań tej samej wizyty jako jeden rekord.

    Tokeny i koszt się sumują — rachunek przychodzi za oba wywołania. Tokeny audio
    NIE: oba modele słuchały tego samego nagrania, a z tej liczby aplikacja odtwarza
    długość wizyty, więc suma dawałaby wizytę dwa razy dłuższą niż w rzeczywistości.
    Cena jest znana tylko wtedy, gdy znamy ją dla obu modeli.
    """
    if not secondary:
        return dict(primary)
    merged = dict(primary)
    for key in ("prompt_tokens", "output_tokens", "thoughts_tokens", "total_tokens", "prompt_text_tokens"):
        merged[key] = int(primary.get(key, 0) or 0) + int(secondary.get(key, 0) or 0)
    merged["estimated_cost_usd"] = round(
        float(primary.get("estimated_cost_usd", 0.0) or 0.0)
        + float(secondary.get("estimated_cost_usd", 0.0) or 0.0),
        6,
    )
    merged["prompt_audio_tokens"] = max(
        int(primary.get("prompt_audio_tokens", 0) or 0),
        int(secondary.get("prompt_audio_tokens", 0) or 0),
    )
    merged["modality_known"] = bool(primary.get("modality_known")) and bool(
        secondary.get("modality_known")
    )
    merged["pricing_known"] = bool(primary.get("pricing_known", True)) and bool(
        secondary.get("pricing_known", True)
    )
    models = [m for m in (primary.get("model"), secondary.get("model")) if m]
    merged["model"] = " + ".join(dict.fromkeys(models))
    return merged
