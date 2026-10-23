from functools import lru_cache
from pathlib import Path

from google import genai
from google.genai import types

from .audio import resolve_audio_mime
from .config import (
    GCP_LOCATION,
    GCP_PROJECT_ID,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_THINKING_BUDGET,
    TRANSCRIPTION_MODEL,
    USE_VERTEX_AI,
    _gcp_credentials_info,
    thinking_budget_for,
)
from .pricing import estimate_usage_and_cost
from .prompts import NO_SPEECH_MARKER, SYSTEM_PROMPT, TRANSCRIPTION_PROMPT, build_user_prompt
from .schemas import DokumentacjaDraft, DokumentacjaSekcje


def _vertex_credentials():
    """Zbuduj poświadczenia service account z secrets — bez polegania na ADC/metadata server."""
    try:
        info = _gcp_credentials_info()
    except Exception as e:
        raise RuntimeError(
            "Nie udało się sparsować poświadczeń service account. "
            "Wklej cały plik JSON jako GOOGLE_APPLICATION_CREDENTIALS_JSON "
            "w potrójnych cudzysłowach, albo użyj sekcji [gcp_service_account] "
            f"z polami pole-po-polu. Szczegóły: {e}"
        ) from e
    if info is None:
        return None
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    if USE_VERTEX_AI:
        if not GCP_PROJECT_ID:
            raise RuntimeError(
                "USE_VERTEX_AI=true, ale brak GCP_PROJECT_ID. "
                "Uzupełnij sekrety (GCP_PROJECT_ID, GCP_LOCATION, GOOGLE_APPLICATION_CREDENTIALS_JSON)."
            )
        creds = _vertex_credentials()
        if creds is None:
            raise RuntimeError(
                "USE_VERTEX_AI=true, ale brak poświadczeń service account w sekretach. "
                "Wklej cały plik JSON jako GOOGLE_APPLICATION_CREDENTIALS_JSON "
                "(między potrójnymi cudzysłowami), albo użyj sekcji [gcp_service_account]."
            )
        return genai.Client(
            vertexai=True,
            project=GCP_PROJECT_ID,
            location=GCP_LOCATION,
            credentials=creds,
        )

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "Brak GEMINI_API_KEY. Skopiuj .env.example do .env i uzupełnij klucz, "
            "albo włącz Vertex AI (USE_VERTEX_AI=true)."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _generation_config(schema) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
        thinking_config=types.ThinkingConfig(
            thinking_budget=thinking_budget_for(GEMINI_MODEL, GEMINI_THINKING_BUDGET)
        ),
    )


def _transcription_config() -> types.GenerateContentConfig:
    # Bez myślenia: przepisanie rozmowy nie wymaga rozumowania, a każdy token myślenia
    # to czas, na który czeka lekarz. Gdyby ktoś ustawił model, który myślenia wyłączyć
    # nie umie, `thinking_budget_for` podniesie budżet do jego minimum.
    return types.GenerateContentConfig(
        system_instruction=TRANSCRIPTION_PROMPT,
        temperature=0.0,
        thinking_config=types.ThinkingConfig(
            thinking_budget=thinking_budget_for(TRANSCRIPTION_MODEL, 0)
        ),
    )


def _usage_of(response, default_model: str) -> dict:
    """Zużycie i koszt, wyceniane po modelu, który FAKTYCZNIE odpowiedział.

    `model_version` bierzemy przed nazwą z konfiguracji, bo ta może wskazywać alias
    (np. `…-latest`), a rachunek wystawia się za wydanie, które policzyło.
    """
    return estimate_usage_and_cost(
        getattr(response, "usage_metadata", None),
        model=getattr(response, "model_version", None) or default_model,
    )


def _audio_part(audio_path: Path, mime_type: str | None):
    return types.Part.from_bytes(
        data=audio_path.read_bytes(),
        mime_type=resolve_audio_mime(audio_path.suffix, mime_type),
    )


def _parse(response, schema):
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    try:
        return schema.model_validate_json(response.text)
    except ValueError as exc:
        # Szczegółowa dokumentacja jest długa. Gdy model urwie odpowiedź w pół (limit
        # tokenów, przerwane połączenie), JSON jest niedomknięty — bez tej informacji
        # lekarz zobaczyłby tylko niezrozumiały błąd walidacji.
        reason = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = str(getattr(candidates[0], "finish_reason", "") or "")
        raise RuntimeError(
            "Model zwrócił niekompletną dokumentację"
            + (f" (powód zakończenia: {reason})" if reason else "")
            + ". Spróbuj wygenerować ponownie."
        ) from exc


def generate_documentation(
    audio_path: Path,
    few_shot_examples: list[dict],
    *,
    mime_type: str | None = None,
    visit_type: str | None = None,
    with_transcript: bool = True,
):
    """Dokumentacja z nagrania, wysłanego inline.

    `with_transcript=True` — model pisze też transkrypcję (odpowiedź `DokumentacjaDraft`).
    `with_transcript=False` — tylko sekcje (`DokumentacjaSekcje`), bo transkrypcję robi
    równolegle osobny model. Inline działa i na Vertex AI, i na Developer API — Vertex
    nie ma Files API, więc to jedyna ścieżka wspólna dla obu.
    """
    schema = DokumentacjaDraft if with_transcript else DokumentacjaSekcje
    prompt = build_user_prompt(
        few_shot_examples, transcript=None, visit_type=visit_type, with_transcript=with_transcript
    )
    response = _client().models.generate_content(
        model=GEMINI_MODEL,
        contents=[prompt, _audio_part(audio_path, mime_type)],
        config=_generation_config(schema),
    )
    return _parse(response, schema), prompt, _usage_of(response, GEMINI_MODEL)


def transcribe(audio_path: Path, *, mime_type: str | None = None) -> tuple[str, dict]:
    """Dosłowna transkrypcja szybszym modelem — zwykły tekst, bez schematu JSON."""
    response = _client().models.generate_content(
        model=TRANSCRIPTION_MODEL,
        contents=[_audio_part(audio_path, mime_type)],
        config=_transcription_config(),
    )
    text = (response.text or "").strip()
    if text.strip(" .") == NO_SPEECH_MARKER:
        text = ""
    return text, _usage_of(response, TRANSCRIPTION_MODEL)


def generate_note_from_text(
    transcript: str,
    few_shot_examples: list[dict],
    *,
    visit_type: str | None = None,
) -> tuple[DokumentacjaDraft, str, dict]:
    """Ścieżka zarezerwowana na przyszły podział: osobna transkrypcja → dokumentacja z tekstu."""
    client = _client()
    prompt = build_user_prompt(few_shot_examples, transcript=transcript, visit_type=visit_type)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[prompt],
        config=_generation_config(DokumentacjaDraft),
    )
    return _parse(response, DokumentacjaDraft), prompt, _usage_of(response, GEMINI_MODEL)
