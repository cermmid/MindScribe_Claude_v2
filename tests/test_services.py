"""Testy reguł dokumentacji: schemat, prompt, składanie tekstu, zatwierdzanie, odczyt.

Najważniejsze są te, które chronią pacjenta przed cichym błędem w dokumentacji:
brak rozmowy o ryzyku nigdy nie staje się „zaprzecza", nieporuszony obszar jest
oznaczony zamiast pominięty, a lista do weryfikacji i transkrypcja nie trafiają
do tekstu, który lekarz wkleja do dokumentacji medycznej.
"""

import io
import json
import wave
from array import array

import pytest
from pydantic import ValidationError

from src.audio import looks_silent, resolve_audio_mime
from src.config import _as_int, thinking_budget_for
from src.formatting import (
    J_NIE_OCENIANO,
    NIE_PORUSZONO,
    PILNE,
    audio_quality_label,
    audio_unusable,
    draft_to_document,
    risk_is_urgent,
    risk_status,
    with_header,
)
from src.pricing import estimate_audio_seconds, format_duration
from src.prompts import SYSTEM_PROMPT, build_few_shot_block, build_user_prompt
from src.schemas import SEKCJE, DokumentacjaDraft, Ryzyko
from src.services import (
    build_approved_documentation,
    derive_audio_suffix,
    resolve_note_version,
)


def _draft(**overrides) -> dict:
    """Szkic w takim kształcie, w jakim zapisuje go aplikacja (`model_dump(mode="json")`)."""
    base = DokumentacjaDraft(
        jakosc_nagrania="DOBRA",
        raw_transcript="transkrypcja",
        notatka="Pacjent zgłasza się na wizytę kontrolną.",
        bezpieczenstwo={"status": "NIE_OCENIANO"},
    ).model_dump(mode="json")
    base.update(overrides)
    return base


def _legacy_draft(**overrides) -> dict:
    """Szkic zapisany w poprzednim formacie: każdy obszar A–Q w osobnym polu."""
    base = {field: "" for field, _ in SEKCJE}
    base.update(
        jakosc_nagrania="DOBRA",
        raw_transcript="transkrypcja",
        e_leki=[],
        j_bezpieczenstwo={"status": "NIE_OCENIANO", "opis": "", "pilne": False},
        do_weryfikacji=[],
    )
    base.update(overrides)
    return base


# --- Schemat ---------------------------------------------------------------------


class TestSchema:
    def test_risk_has_three_states(self):
        """Trzeci stan jest powodem zmiany: brak rozmowy o ryzyku to nie zaprzeczenie."""
        assert {r.value for r in Ryzyko} == {"OBECNE", "ZAPRZECZA", "NIE_OCENIANO"}

    def test_note_defaults_to_empty(self):
        """Pusta notatka jest poprawną odpowiedzią — np. przy niemym nagraniu."""
        draft = DokumentacjaDraft(
            jakosc_nagrania="DOBRA", raw_transcript="", bezpieczenstwo={"status": "NIE_OCENIANO"}
        )
        assert draft.notatka == ""
        assert draft.do_weryfikacji == []

    def test_transcript_is_generated_before_the_note(self):
        """Gemini generuje pola po kolei — notatka ma powstawać z transkrypcją przed oczami."""
        fields = list(DokumentacjaDraft.model_fields)
        assert fields.index("raw_transcript") < fields.index("notatka")

    def test_note_is_one_field_not_sections(self):
        """Notatka to jeden tekst lekarza — schemat nie może wymuszać podziału na sekcje."""
        fields = set(DokumentacjaDraft.model_fields)
        assert "notatka" in fields
        assert not fields & {field for field, _ in SEKCJE}

    def test_review_list_is_generated_last(self):
        """Kontrola kompletności z punktu 6 ma nastąpić po napisaniu całości."""
        assert list(DokumentacjaDraft.model_fields)[-1] == "do_weryfikacji"

    def test_no_diagnosis_or_suggestion_fields(self):
        """Prompt: „nie stawiaj rozpoznań, nie twórz planów leczenia"."""
        fields = set(DokumentacjaDraft.model_fields)
        assert not {f for f in fields if "icd" in f or "rozpozn" in f or "propon" in f}


# --- Prompt ----------------------------------------------------------------------


class TestPrompt:
    def test_clinical_prompt_is_loaded_verbatim(self):
        """Treść lekarza trafia do modelu w całości — zasada nadrzędna i zasada końcowa."""
        assert "nie skracaj wywiadu kosztem informacji klinicznie istotnych" in SYSTEM_PROMPT
        assert "Nie stawiaj samodzielnie rozpoznań" in SYSTEM_PROMPT
        assert "Brak danych nie oznacza zaprzeczenia objawom" in SYSTEM_PROMPT

    def test_technical_rules_forbid_denial_without_question(self):
        assert "Wybierz ZAPRZECZA wyłącznie" in SYSTEM_PROMPT
        assert "NIE_OCENIANO" in SYSTEM_PROMPT

    def test_silent_recording_rule_is_present(self):
        assert "BRAK_MOWY" in SYSTEM_PROMPT

    def test_note_form_is_continuous_text(self):
        """Obszary A–Q to lista kontrolna, nie nagłówki — tak mówi prompt, nie tylko schemat."""
        assert "To NIE są nagłówki" in SYSTEM_PROMPT
        assert "bez nagłówków" in SYSTEM_PROMPT
        assert "według poniższej struktury" not in SYSTEM_PROMPT

    def test_prompt_file_has_no_invisible_characters(self):
        """Wklejony tekst miał miękki dzielnik w słowie „Dawkę" — niewidoczny, ale w tokenach."""
        assert "­" not in SYSTEM_PROMPT

    def test_visit_type_reaches_the_model(self):
        assert "Pierwsza wizyta" in build_user_prompt([], visit_type="Pierwsza")
        assert "Wizyta kolejna" in build_user_prompt([], visit_type="Kolejna")


class TestFewShot:
    def _approved(self, dokument: str) -> dict:
        return {"note_json": json.dumps({"dokument": dokument, "ryzyko": "ZAPRZECZA"})}

    def test_uses_approved_document(self):
        block = build_few_shot_block([self._approved("Pacjent zgłasza trudności ze snem.")])
        assert "Pacjent zgłasza trudności ze snem." in block

    def test_transcript_never_reaches_the_prompt(self):
        example = self._approved("Dokument.") | {"raw_transcript": "TAJNA ROZMOWA"}
        assert "TAJNA ROZMOWA" not in build_few_shot_block([example])

    def test_unreadable_or_foreign_format_is_skipped(self):
        """Zapis w innym formacie nie może wejść do promptu jako pusty albo surowy JSON."""
        examples = [{"note_json": "{zły json"}, {"note_json": json.dumps({"podsumowanie": "x"})}]
        assert build_few_shot_block(examples) == ""

    def test_no_examples_no_block(self):
        assert build_few_shot_block([]) == ""

    def test_sectioned_examples_are_skipped(self):
        """Stara dokumentacja z nagłówkami A–Q ciągnęłaby model z powrotem do sekcji."""
        old = "A. Dane ogólne\nx\n\nB. Główny powód\ny\n\nC. Objawy\nz"
        block = build_few_shot_block([self._approved(old), self._approved("Notatka ciągłym tekstem.")])
        assert "Notatka ciągłym tekstem." in block
        assert "A. Dane ogólne" not in block


# --- Składanie dokumentu -----------------------------------------------------------


class TestDraftToDocument:
    def test_note_is_the_document(self):
        doc = draft_to_document(_draft(notatka="Pacjent opisuje trudności z zasypianiem.\n\nZalecono kontrolę."))
        assert doc == "Pacjent opisuje trudności z zasypianiem.\n\nZalecono kontrolę.\n"

    def test_no_section_headings_or_not_discussed_markers(self):
        """Notatka lekarza, nie formularz: bez nagłówków A–Q i bez „nie poruszono"."""
        doc = draft_to_document(_draft())
        assert NIE_PORUSZONO not in doc
        assert not any(title in doc for _, title in SEKCJE)

    def test_missing_risk_section_defaults_to_unassessed(self):
        """Uszkodzony albo niekompletny zapis nie może dać „zaprzecza" przez przypadek."""
        assert risk_status({}) == "NIE_OCENIANO"
        assert risk_status({"bezpieczenstwo": {"status": "COS"}}) == "NIE_OCENIANO"
        assert risk_status(_draft(bezpieczenstwo={"status": "ZAPRZECZA"})) == "ZAPRZECZA"

    def test_urgent_flag_is_read_for_the_banner(self):
        assert risk_is_urgent(_draft(bezpieczenstwo={"status": "OBECNE", "pilne": True}))

    def test_review_list_stays_out_of_the_document(self):
        """To zadania dla lekarza, nie treść dokumentacji medycznej."""
        doc = draft_to_document(_draft(do_weryfikacji=["Dawka sertraliny niejasna."]))
        assert "Dawka sertraliny niejasna" not in doc

    def test_transcript_stays_out_of_the_document(self):
        doc = draft_to_document(_draft(raw_transcript="PEŁNY ZAPIS ROZMOWY"))
        assert "PEŁNY ZAPIS ROZMOWY" not in doc

    def test_failed_recording_is_announced_first(self):
        """Inaczej pusta notatka wygląda jak wizyta, na której niczego nie omówiono."""
        doc = draft_to_document(_draft(jakosc_nagrania="BRAK_MOWY", raw_transcript="", notatka=""))
        assert doc.startswith("!! UWAGA")

    def test_poor_recording_warning_precedes_the_note(self):
        doc = draft_to_document(_draft(jakosc_nagrania="SLABA", notatka="Treść."))
        assert doc.startswith("!! UWAGA")
        assert doc.rstrip().endswith("Treść.")


class TestLegacyDrafts:
    """Szkice zapisane przed przejściem na notatkę — historia ma je dalej pokazywać."""

    def test_rendered_with_sections_in_order(self):
        doc = draft_to_document(_legacy_draft(c_aktualne_objawy="Objawy."))
        positions = [doc.index(title) for _, title in SEKCJE]
        assert positions == sorted(positions)
        assert "Objawy." in doc

    def test_unassessed_risk_never_reads_as_denial(self):
        doc = draft_to_document(_legacy_draft())
        assert J_NIE_OCENIANO in doc
        assert "zaprzecza" not in doc.lower()

    def test_urgent_flag_and_medications_survive(self):
        doc = draft_to_document(
            _legacy_draft(
                j_bezpieczenstwo={"status": "OBECNE", "opis": "Plan.", "pilne": True},
                e_leki=[{"nazwa": "sertralina", "opis": "50 mg"}],
            )
        )
        assert PILNE in doc
        assert "1. sertralina\n50 mg" in doc
        assert risk_status(_legacy_draft(j_bezpieczenstwo={"status": "OBECNE"})) == "OBECNE"

    def test_history_resolves_legacy_draft(self):
        resolved = resolve_note_version({"ai_note_original_json": json.dumps(_legacy_draft(b_powod_zgloszenia="Stary szkic."))})
        assert resolved.document is not None
        assert "Stary szkic." in resolved.document

    def test_header_comes_from_the_record(self):
        text = with_header(
            "A. Treść\n", title="Wizyta #3", visit_type="Kolejna", created_at="2026-10-23T10:00:00",
            doctor_name="dr Nowak",
        )
        assert text.startswith("Wizyta #3\nWizyta kolejna · Data: 2026-10-23 · Prowadzący: dr Nowak")
        assert text.rstrip().endswith("A. Treść")


# --- Zatwierdzanie -----------------------------------------------------------------


class TestApprovedDocumentation:
    def test_valid_document_is_accepted(self):
        approved = build_approved_documentation(dokument="  Treść  ", ryzyko="ZAPRZECZA")
        assert approved.dokument == "Treść"
        assert approved.ryzyko is Ryzyko.ZAPRZECZA

    def test_empty_document_is_rejected(self):
        """Zatwierdzona dokumentacja uczy model stylu — pusta nauczyłaby pustych odpowiedzi."""
        with pytest.raises(ValidationError):
            build_approved_documentation(dokument="   \n ", ryzyko="ZAPRZECZA")

    def test_unknown_risk_value_is_rejected(self):
        with pytest.raises(ValueError):
            build_approved_documentation(dokument="Treść", ryzyko="NIEOBECNE")


class TestApproveFailsLoudly:
    """Zatwierdzenie, które niczego nie zmieniło, nie może meldować sukcesu."""

    def _approved(self):
        return build_approved_documentation(dokument="Treść dokumentacji.", ryzyko="NIE_OCENIANO")

    def _insert(self, temp_db, owner: str) -> int:
        return temp_db.insert_visit(
            audio_path=None,
            pipeline="multimodal",
            raw_transcript="t",
            ai_note_original_json="{}",
            doctor_id=owner,
        )

    def test_raises_when_visit_does_not_exist(self, temp_db):
        from src.services import VisitNotUpdated, approve_documentation

        with pytest.raises(VisitNotUpdated):
            approve_documentation(999_999, self._approved(), doctor_id="user-a")

    def test_raises_when_visit_belongs_to_someone_else(self, temp_db):
        from src.services import VisitNotUpdated, approve_documentation

        visit_id = self._insert(temp_db, "user-b")
        with pytest.raises(VisitNotUpdated):
            approve_documentation(visit_id, self._approved(), doctor_id="user-a")

    def test_succeeds_and_feeds_few_shot(self, temp_db):
        from src.services import approve_documentation, load_few_shot_examples

        visit_id = self._insert(temp_db, "user-a")
        approve_documentation(visit_id, self._approved(), doctor_id="user-a")
        assert temp_db.get_visit(visit_id, doctor_id="user-a")["status"] == "approved"
        assert "Treść dokumentacji." in build_few_shot_block(load_few_shot_examples("user-a"))

    def test_few_shot_never_carries_transcripts(self, temp_db):
        """Transkrypcji nie pobieramy z bazy wcale, skoro nie trafia do promptu."""
        from src.services import approve_documentation, load_few_shot_examples

        visit_id = self._insert(temp_db, "user-a")
        approve_documentation(visit_id, self._approved(), doctor_id="user-a")
        assert all("raw_transcript" not in ex for ex in load_few_shot_examples("user-a"))


# --- Odczyt ----------------------------------------------------------------------


class TestResolveNoteVersion:
    def test_approved_document_wins(self):
        visit = {
            "ai_note_original_json": json.dumps(_draft(notatka="wersja asystenta")),
            "doctor_note_corrected_json": json.dumps({"dokument": "wersja lekarza", "ryzyko": "OBECNE"}),
        }
        resolved = resolve_note_version(visit)
        assert resolved.is_approved is True
        assert resolved.document.strip() == "wersja lekarza"
        assert resolved.ryzyko == "OBECNE"

    def test_falls_back_to_rendered_draft(self):
        visit = {"ai_note_original_json": json.dumps(_draft(notatka="wersja asystenta"))}
        resolved = resolve_note_version(visit)
        assert resolved.is_approved is False
        assert "wersja asystenta" in resolved.document

    def test_foreign_format_is_shown_raw_not_as_empty(self):
        """Zapis spoza tego schematu nie może udawać pustej dokumentacji."""
        visit = {"ai_note_original_json": json.dumps({"podsumowanie": "stary format"})}
        resolved = resolve_note_version(visit)
        assert resolved.document is None
        assert "stary format" in resolved.source_json

    def test_broken_json_does_not_raise(self):
        resolved = resolve_note_version({"ai_note_original_json": "{to nie jest json"})
        assert resolved.document is None
        assert resolved.source_json == "{to nie jest json"

    def test_no_note_at_all(self):
        resolved = resolve_note_version({})
        assert resolved.document is None
        assert resolved.source_json is None


# --- Widok: escapowanie i podział na sekcje ------------------------------------------


class TestDocumentView:
    def test_clinical_text_is_not_turned_into_markdown(self):
        """Gwiazdka w wypowiedzi pacjenta nie może zamienić się w pogrubienie."""
        from src.ui import _escape

        assert _escape("*bardzo* źle") == r"\*bardzo\* źle"

    def test_line_breaks_survive(self):
        from src.ui import _escape

        assert "  \n" in _escape("linia pierwsza\nlinia druga")

    def test_note_is_shown_as_one_block(self):
        from src.ui import _split_sections

        doc = draft_to_document(_draft(notatka="Akapit pierwszy.\n\nAkapit drugi."))
        assert _split_sections(doc) == [(None, "Akapit pierwszy.\n\nAkapit drugi.")]

    def test_edited_headings_do_not_lose_content(self):
        """Lekarz mógł przeredagować nagłówki — wtedy treść ma zostać, choć bez podziału."""
        from src.ui import _split_sections

        sections = _split_sections("Moje własne nagłówki\nTreść wizyty.")
        assert sections == [(None, "Moje własne nagłówki\nTreść wizyty.")]


# --- Audio, konfiguracja, czas ------------------------------------------------------


class TestAudioQualityFlags:
    def test_flags_unusable_recording(self):
        draft = {"jakosc_nagrania": "BRAK_MOWY"}
        assert audio_unusable(draft) is True
        assert "nie wykryto zrozumiałej mowy" in audio_quality_label(draft)

    def test_flags_poor_recording_without_marking_unusable(self):
        draft = {"jakosc_nagrania": "SLABA"}
        assert audio_unusable(draft) is False
        assert audio_quality_label(draft) is not None

    def test_good_recording_has_no_warning(self):
        assert audio_quality_label({"jakosc_nagrania": "DOBRA"}) is None


def _wav_bytes(samples: list[int], *, framerate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(array("h", samples).tobytes())
    return buf.getvalue()


class TestLooksSilent:
    """Wykrywanie martwego mikrofonu — przeglądarka nagrywa, ale nie dostaje sygnału."""

    def test_empty_payload_is_silent(self):
        assert looks_silent(b"") is True

    def test_tiny_payload_is_silent(self):
        assert looks_silent(b"RIFF" + b"\x00" * 100) is True

    def test_wav_of_pure_zeros_is_silent(self):
        assert looks_silent(_wav_bytes([0] * 16000)) is True

    def test_wav_with_speech_level_audio_is_not_silent(self):
        loud = [12000 if i % 2 else -12000 for i in range(16000)]
        assert looks_silent(_wav_bytes(loud)) is False

    def test_faint_background_noise_still_counts_as_silent(self):
        """Kilka jednostek szumu kwantyzacji to nadal martwy mikrofon."""
        assert looks_silent(_wav_bytes([1, -1] * 8000)) is True

    def test_quiet_but_real_speech_passes(self):
        """Cicha mowa musi przejść — próg nie może blokować normalnego nagrania."""
        quiet = [400 if i % 2 else -400 for i in range(16000)]
        assert looks_silent(_wav_bytes(quiet)) is False

    def test_undecodable_container_judged_only_by_size(self):
        """Webm/mp4 nie dekodujemy — lepiej przepuścić niż zablokować dobre nagranie."""
        assert looks_silent(b"\x1aE\xdf\xa3" + b"\x00" * 50_000, "audio/webm") is False


class TestResolveAudioMime:
    """Zły typ MIME sprawiał, że model zmyślał transkrypcję."""

    def test_browser_mime_wins_over_extension(self):
        assert resolve_audio_mime(".wav", "audio/webm") == "audio/webm"

    def test_strips_codec_parameters(self):
        assert resolve_audio_mime(".wav", "audio/webm;codecs=opus") == "audio/webm"

    def test_accepts_video_container_for_audio_only_recording(self):
        assert resolve_audio_mime(".webm", "video/webm") == "video/webm"

    def test_falls_back_to_extension(self):
        assert resolve_audio_mime(".mp3", None) == "audio/mpeg"
        assert resolve_audio_mime(".m4a", None) == "audio/mp4"

    def test_ignores_nonsense_declared_type(self):
        assert resolve_audio_mime(".mp3", "application/octet-stream") == "audio/mpeg"

    def test_unknown_extension_defaults_to_wav(self):
        assert resolve_audio_mime(".xyz", None) == "audio/wav"
        assert resolve_audio_mime(None, None) == "audio/wav"


class TestDeriveAudioSuffix:
    def test_takes_extension_from_filename(self):
        assert derive_audio_suffix("wizyta.MP3") == ".mp3"

    def test_defaults_when_no_extension(self):
        assert derive_audio_suffix("nagranie") == ".wav"

    def test_defaults_when_no_filename(self):
        assert derive_audio_suffix(None) == ".wav"


class TestThinkingBudget:
    """Model Pro nie umie wyłączyć myślenia — zły budżet ma być przycięty, nie odrzucony."""

    def test_pro_budget_is_raised_to_the_minimum(self):
        assert thinking_budget_for("gemini-2.5-pro", 0) == 128
        assert thinking_budget_for("gemini-2.5-pro", 64) == 128

    def test_valid_budget_passes_through(self):
        assert thinking_budget_for("gemini-2.5-pro", 8192) == 8192

    def test_dynamic_budget_is_left_alone(self):
        assert thinking_budget_for("gemini-2.5-pro", -1) == -1

    def test_flash_may_disable_thinking(self):
        assert thinking_budget_for("gemini-2.5-flash", 0) == 0


class TestIntConfigParsing:
    """Literówka w sekrecie nie może wywalić startu aplikacji."""

    def test_parses_a_number(self):
        assert _as_int("2048", 8192) == 2048

    @pytest.mark.parametrize("bad", ["", None, "abc", "  "])
    def test_falls_back_on_junk(self, bad):
        assert _as_int(bad, 8192) == 8192

    def test_allows_dynamic_sentinel(self):
        assert _as_int("-1", 8192) == -1


class TestEstimateAudioSeconds:
    def test_estimates_from_audio_tokens(self):
        """32 tokeny na sekundę: 57 600 tokenów to pół godziny."""
        assert estimate_audio_seconds(57_600) == 1800.0

    def test_none_when_no_data(self):
        assert estimate_audio_seconds(0) is None
        assert estimate_audio_seconds(None) is None

    def test_none_when_modality_unknown(self):
        assert estimate_audio_seconds(57_600, modality_known=False) is None

    def test_none_below_trust_threshold(self):
        assert estimate_audio_seconds(3_840) is None  # ~2 minuty


class TestFormatDuration:
    def test_minutes(self):
        assert format_duration(840) == "14 min"

    def test_hours_and_minutes(self):
        assert format_duration(4980) == "1 h 23 min"

    def test_missing(self):
        assert format_duration(None) == "—"
        assert format_duration(0) == "—"
