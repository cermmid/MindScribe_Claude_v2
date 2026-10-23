"""Warstwa serwisowa — logika przypadków użycia, bez żadnej zależności od UI.

Ten moduł celowo **nie importuje streamlita ani pandas**, żeby dało się go wywołać tak
samo z Streamlita, jak i z przyszłego backendu.

Reguły zakodowane w tym pliku:
- wizyta zostaje `approved` WYŁĄCZNIE po walidacji `ZatwierdzonaDokumentacja` — pustego
  dokumentu nie da się zatwierdzić,
- status ryzyka samobójczego jest jawnie potwierdzany przez lekarza i ma trzy stany;
  brak danych nigdy nie zamienia się w „zaprzecza",
- przy odczycie wygrywa wersja zatwierdzona przez lekarza, a szkic modelu jest fallbackiem.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .audio import save_uploaded_audio
from .db import get_approved_examples, get_visit, insert_visit, update_visit
from .formatting import draft_to_document, risk_status
from .schemas import DokumentacjaDraft, JakoscNagrania, Ryzyko, ZatwierdzonaDokumentacja

# `gemini_client` ciągnie za sobą cały SDK Google, więc importujemy go leniwie —
# czyste reguły z tego modułu dają się wtedy testować bez SDK.

DEFAULT_AUDIO_SUFFIX = ".wav"


def derive_audio_suffix(filename: str | None, *, default: str = DEFAULT_AUDIO_SUFFIX) -> str:
    """Rozszerzenie pliku audio na podstawie nazwy uploadu.

    Nagranie z mikrofonu nie ma nazwy — wtedy zwracamy domyślne `.wav`.
    """
    if not filename or "." not in filename:
        return default
    ext = filename.rsplit(".", 1)[-1].strip().lower()
    return f".{ext}" if ext else default


# --- Przypadek użycia: utworzenie wizyty ---------------------------------------


@dataclass
class Timings:
    """Ile zajął każdy etap generowania, w sekundach.

    Rozbicie, nie jedna liczba: „trwa za długo" nie mówi, czy winien jest model, czy
    zapis — a każda z tych rzeczy naprawia się inaczej. Szczegółowa dokumentacja jest
    kilka razy dłuższa niż dawna notatka, więc czas modelu jest tu głównym kosztem.
    """

    zapis_audio: float = 0.0
    model: float = 0.0
    calosc: float = 0.0

    def summary(self) -> str:
        return f"model {self.model:.0f} s · zapis {self.zapis_audio:.0f} s"


@dataclass
class CreatedVisit:
    visit_id: int
    draft: DokumentacjaDraft
    debug_prompt: str
    usage: dict[str, Any] = field(default_factory=dict)
    few_shot_count: int = 0
    timings: Timings = field(default_factory=Timings)

    @property
    def document(self) -> str:
        """Szkic jako jeden tekst — to lekarz dostaje do poprawienia."""
        return draft_to_document(self.draft.model_dump(mode="json"))


def load_few_shot_examples(doctor_id: str) -> list[dict[str, str]]:
    """Zatwierdzona dokumentacja TEJ osoby, zasilająca few-shot.

    Właściciel jest wymagany: to najgroźniejsze miejsce w całej aplikacji. Bez
    filtra dokumentacja pacjenta jednej osoby trafiłaby do zapytania drugiej.
    """
    return get_approved_examples(doctor_id=doctor_id)


def create_visit_from_audio(
    audio_bytes: bytes,
    *,
    audio_suffix: str = DEFAULT_AUDIO_SUFFIX,
    audio_mime: str | None = None,
    visit_label: str | None = None,
    visit_type: str | None = None,
    doctor_id: str,
    doctor_name: str | None = None,
    few_shot: list[dict[str, str]] | None = None,
    pipeline: str = "multimodal",
) -> CreatedVisit:
    """Pełna ścieżka: zapis audio → few-shot → Gemini → zapis wizyty jako szkic.

    `audio_mime` to typ zgłoszony przez przeglądarkę. Przekazuj go zawsze, gdy jest
    dostępny — bez niego zgadujemy z rozszerzenia, a przy nagraniu z mikrofonu
    rozszerzenie bywa nieprawdziwe.

    Wyjątki z wywołania Gemini są propagowane — wołający decyduje, jak je pokazać.
    """
    from .gemini_client import generate_note_from_audio

    if not audio_bytes:
        raise ValueError("Brak danych audio.")

    if few_shot is None:
        few_shot = load_few_shot_examples(doctor_id)

    started = time.monotonic()
    audio_path: Path = save_uploaded_audio(audio_bytes, suffix=audio_suffix)
    after_save = time.monotonic()

    draft, debug_prompt, usage = generate_note_from_audio(
        audio_path, few_shot, mime_type=audio_mime, visit_type=visit_type
    )
    after_model = time.monotonic()

    timings = Timings(
        zapis_audio=after_save - started,
        model=after_model - after_save,
        calosc=after_model - started,
    )

    visit_id = insert_visit(
        audio_path=str(audio_path),
        pipeline=pipeline,
        raw_transcript=draft.raw_transcript,
        ai_note_original_json=draft.model_dump_json(indent=2),
        visit_label=(visit_label or "").strip() or None,
        visit_type=visit_type,
        doctor_id=doctor_id,
        doctor_name=doctor_name,
        usage=usage,
        generation_seconds=round(timings.calosc, 2),
    )
    return CreatedVisit(
        visit_id=visit_id,
        draft=draft,
        debug_prompt=debug_prompt,
        usage=usage,
        few_shot_count=len(few_shot),
        timings=timings,
    )


# --- Przypadek użycia: zatwierdzenie dokumentacji ------------------------------


def build_approved_documentation(
    *,
    dokument: str,
    ryzyko: str,
    jakosc_nagrania: str = "DOBRA",
) -> ZatwierdzonaDokumentacja:
    """Zwaliduj tekst poprawiony przez lekarza. Rzuca `ValueError`, gdy dane są złe.

    Pusty dokument jest odrzucany: zatwierdzona dokumentacja zasila few-shot, więc
    pusty wpis uczyłby model, że pusta odpowiedź jest akceptowalnym stylem.
    """
    return ZatwierdzonaDokumentacja(
        dokument=(dokument or "").strip(),
        ryzyko=Ryzyko(ryzyko),
        jakosc_nagrania=JakoscNagrania(jakosc_nagrania or "DOBRA"),
    )


class VisitNotUpdated(RuntimeError):
    """Zatwierdzenie nie zmieniło żadnego wiersza — wizyta nie istnieje albo nie należy do tej osoby."""


def approve_documentation(
    visit_id: int, approved: ZatwierdzonaDokumentacja, *, doctor_id: str
) -> None:
    """Zapisz zatwierdzoną dokumentację i oznacz wizytę jako zatwierdzoną.

    Rzuca `VisitNotUpdated`, gdy nic się nie zmieniło. Bez tego sprawdzenia próba
    zatwierdzenia cudzej (albo nieistniejącej) wizyty kończyłaby się komunikatem
    o sukcesie mimo braku zapisu — a lekarz uznałby dokumentację za zachowaną.
    """
    changed = update_visit(
        visit_id,
        doctor_note_corrected_json=approved.model_dump_json(indent=2),
        status="approved",
        doctor_id=doctor_id,
    )
    if not changed:
        raise VisitNotUpdated(
            f"Nie udało się zatwierdzić wizyty #{visit_id} — nie istnieje albo należy "
            "do kogoś innego."
        )


# --- Przypadek użycia: odczyt wizyty -------------------------------------------


@dataclass
class ResolvedNote:
    """Którą wersję dokumentacji pokazujemy i w jakiej postaci.

    `document` to gotowy tekst — zatwierdzony przez lekarza albo złożony ze szkicu
    modelu. `None`, gdy zapisu nie da się odczytać; wtedy widok pokazuje surowy JSON,
    zamiast udawać, że dokumentacja jest pusta.
    """

    document: str | None
    ryzyko: str | None
    is_approved: bool
    source_json: str | None
    draft: dict[str, Any] | None = None


def _load(source: str | None) -> dict[str, Any] | None:
    try:
        data = json.loads(source) if source else None
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def resolve_note_version(visit: Mapping[str, Any]) -> ResolvedNote:
    """Wersja zatwierdzona przez lekarza, a gdy jej nie ma — szkic modelu."""
    corrected_json = visit.get("doctor_note_corrected_json")
    original_json = visit.get("ai_note_original_json")
    draft = _load(original_json)

    approved = _load(corrected_json)
    if approved and str(approved.get("dokument") or "").strip():
        return ResolvedNote(
            document=str(approved["dokument"]).strip() + "\n",
            ryzyko=str(approved.get("ryzyko") or "NIE_OCENIANO"),
            is_approved=True,
            source_json=corrected_json,
            draft=draft,
        )

    # Szkic rozpoznajemy po obecności sekcji J — tylko schemat tej wersji ją ma.
    # Zapis w innym formacie pokazujemy surowo, zamiast składać z niego pusty dokument.
    if draft and "j_bezpieczenstwo" in draft:
        return ResolvedNote(
            document=draft_to_document(draft),
            ryzyko=risk_status(draft),
            is_approved=False,
            source_json=original_json,
            draft=draft,
        )
    return ResolvedNote(
        document=None,
        ryzyko=None,
        is_approved=False,
        source_json=corrected_json or original_json,
        draft=draft,
    )


def get_visit_with_note(
    visit_id: int, *, doctor_id: str
) -> tuple[dict[str, Any] | None, ResolvedNote | None]:
    """Pobierz wizytę razem z rozstrzygniętą wersją dokumentacji."""
    visit = get_visit(visit_id, doctor_id=doctor_id)
    if not visit:
        return None, None
    return visit, resolve_note_version(visit)
