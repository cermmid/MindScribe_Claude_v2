"""Osobna transkrypcja i logowanie Google.

Transkrypcja: dwa wywołania naraz muszą dać dokładnie ten sam szkic, co jedno —
reszta aplikacji (zapis, widoki, historia) nie może zauważyć różnicy. Awaria
transkrypcji nie może zabrać lekarzowi dokumentacji.

Logowanie: lista dozwolonych adresów jest jedyną rzeczą, która dzieli aplikację
z danymi pacjentów od każdego, kto ma konto Google.
"""

import json
import tomllib
from pathlib import Path

import pytest

from src import config, gemini_client, services
from src.auth import email_allowed, parse_allowed_emails
from src.pricing import merge_usage
from src.prompts import NO_SPEECH_MARKER, build_user_prompt
from src.schemas import DokumentacjaDraft, DokumentacjaSekcje

ROOT = Path(__file__).resolve().parent.parent


# --- Schemat: dwa warianty z tych samych pól ---------------------------------------


class TestSchemaVariants:
    def test_draft_order_quality_then_transcript_then_sections(self):
        """Gemini pisze pola w kolejności schematu: transkrypcja przed sekcjami."""
        fields = list(DokumentacjaDraft.model_fields)
        assert fields[:3] == ["jakosc_nagrania", "raw_transcript", "a_dane_ogolne"]
        assert fields[-1] == "do_weryfikacji"

    def test_sections_variant_has_no_transcript(self):
        """Bez tego pola model dokumentacji nie przepisuje rozmowy — to jest cały zysk czasu."""
        fields = list(DokumentacjaSekcje.model_fields)
        assert "raw_transcript" not in fields
        assert fields[0] == "jakosc_nagrania"
        assert fields[-1] == "do_weryfikacji"
        assert set(DokumentacjaDraft.model_fields) - set(fields) == {"raw_transcript"}

    def test_from_parts_keeps_every_section(self):
        sekcje = DokumentacjaSekcje(
            jakosc_nagrania="DOBRA",
            c_aktualne_objawy="Obniżony nastrój od 3 miesięcy.",
            e_leki=[{"nazwa": "sertralina", "opis": "50 mg rano"}],
            j_bezpieczenstwo={"status": "ZAPRZECZA", "opis": "Zaprzecza myślom S."},
            do_weryfikacji=["E: dawka niepewna"],
        )
        draft = DokumentacjaDraft.from_parts(sekcje, "  Lekarz: Dzień dobry.\n")
        assert draft.raw_transcript == "Lekarz: Dzień dobry."
        assert draft.model_dump(exclude={"raw_transcript"}) == sekcje.model_dump()

    def test_from_parts_accepts_missing_transcript(self):
        sekcje = DokumentacjaSekcje(jakosc_nagrania="DOBRA", j_bezpieczenstwo={"status": "NIE_OCENIANO"})
        assert DokumentacjaDraft.from_parts(sekcje, None).raw_transcript == ""


# --- Polecenie dla modelu dokumentacji ------------------------------------------


class TestPrompt:
    def test_separate_mode_does_not_ask_for_transcript(self):
        """Prośba o pole, którego nie ma w schemacie, myli model i marnuje tokeny."""
        prompt = build_user_prompt([], with_transcript=False)
        assert "raw_transcript" not in prompt
        assert "osobny" in prompt

    def test_single_call_mode_still_asks_for_transcript(self):
        assert "raw_transcript" in build_user_prompt([], with_transcript=True)


# --- Przełącznik ---------------------------------------------------------------


class TestBoolSetting:
    @pytest.mark.parametrize("raw, expected", [("false", False), ("0", False), ("true", True), ("on", True)])
    def test_explicit_value_wins_over_default(self, monkeypatch, raw, expected):
        monkeypatch.setenv("MS_TEST_FLAG", raw)
        assert config._bool_setting("MS_TEST_FLAG", not expected) is expected

    def test_missing_uses_default(self, monkeypatch):
        monkeypatch.delenv("MS_TEST_FLAG", raising=False)
        monkeypatch.setattr(config, "_from_secrets", lambda key: None)
        assert config._bool_setting("MS_TEST_FLAG", True) is True

    def test_toml_false_is_not_swallowed(self, monkeypatch):
        """`False or True` daje True — dlatego przełącznik nie jest czytany przez `or`."""
        monkeypatch.delenv("MS_TEST_FLAG", raising=False)
        monkeypatch.setattr(config, "_from_secrets", lambda key: False)
        assert config._bool_setting("MS_TEST_FLAG", True) is False


# --- Koszty dwóch wywołań --------------------------------------------------------


def _usage(model, *, prompt=1000, audio=900, out=100, thoughts=0, cost=0.01, known=True):
    return {
        "prompt_tokens": prompt,
        "output_tokens": out,
        "thoughts_tokens": thoughts,
        "total_tokens": prompt + out + thoughts,
        "prompt_audio_tokens": audio,
        "prompt_text_tokens": prompt - audio,
        "modality_known": True,
        "estimated_cost_usd": cost,
        "model": model,
        "pricing_known": known,
        "pricing_tier": "standard",
    }


class TestMergeUsage:
    def test_costs_and_tokens_add_up(self):
        merged = merge_usage(
            _usage("gemini-2.5-pro", out=3000, thoughts=8000, cost=0.20),
            _usage("gemini-2.5-flash", out=6000, cost=0.03),
        )
        assert merged["estimated_cost_usd"] == pytest.approx(0.23)
        assert merged["output_tokens"] == 9000
        assert merged["prompt_tokens"] == 2000
        assert merged["model"] == "gemini-2.5-pro + gemini-2.5-flash"

    def test_audio_tokens_are_not_doubled(self):
        """Z tokenów audio liczona jest długość wizyty — oba modele słuchały tego samego nagrania."""
        merged = merge_usage(_usage("a", audio=38_400), _usage("b", audio=38_400))
        assert merged["prompt_audio_tokens"] == 38_400

    def test_unknown_price_of_either_model_is_reported(self):
        merged = merge_usage(_usage("a"), _usage("b", known=False))
        assert merged["pricing_known"] is False

    def test_missing_secondary_is_a_copy(self):
        primary = _usage("gemini-2.5-pro")
        assert merge_usage(primary, None) == primary


# --- Równoległe wywołania w serwisie -----------------------------------------------


def _sekcje(**overrides) -> DokumentacjaSekcje:
    data = {
        "jakosc_nagrania": "DOBRA",
        "b_powod_zgloszenia": "Kontrola leczenia.",
        "j_bezpieczenstwo": {"status": "NIE_OCENIANO"},
    }
    data.update(overrides)
    return DokumentacjaSekcje(**data)


@pytest.fixture
def fake_models(temp_db, tmp_path, monkeypatch):
    """Podmienia oba wywołania Gemini i zapis audio; zwraca dziennik wywołań."""
    calls: dict = {"documentation": [], "transcribe": 0}
    state = {"sekcje": _sekcje(), "transcript": "Lekarz: Jak się Pan czuje?", "fail": None}

    def fake_documentation(audio_path, few_shot, *, mime_type=None, visit_type=None, with_transcript=True):
        calls["documentation"].append(with_transcript)
        if state["fail"] == "documentation":
            raise RuntimeError("dokumentacja padła")
        if with_transcript:
            draft = DokumentacjaDraft.from_parts(state["sekcje"], "transkrypcja z jednego wywołania")
            return draft, "prompt", _usage("gemini-2.5-pro", cost=0.20)
        return state["sekcje"], "prompt", _usage("gemini-2.5-pro", cost=0.20)

    def fake_transcribe(audio_path, *, mime_type=None):
        calls["transcribe"] += 1
        if state["fail"] == "transcribe":
            raise RuntimeError("transkrypcja padła")
        return state["transcript"], _usage("gemini-2.5-flash", cost=0.03)

    audio_file = tmp_path / "nagranie.wav"
    audio_file.write_bytes(b"RIFF")
    monkeypatch.setattr(gemini_client, "generate_documentation", fake_documentation)
    monkeypatch.setattr(gemini_client, "transcribe", fake_transcribe)
    monkeypatch.setattr(services, "save_uploaded_audio", lambda data, suffix: audio_file)
    return calls, state


def _create(separate=True):
    return services.create_visit_from_audio(
        b"audio", doctor_id="lekarz-1", few_shot=[], separate_transcription=separate
    )


class TestParallelGeneration:
    def test_both_models_called_and_merged(self, fake_models, temp_db):
        calls, _ = fake_models
        created = _create()
        assert calls == {"documentation": [False], "transcribe": 1}
        assert created.draft.raw_transcript == "Lekarz: Jak się Pan czuje?"
        assert created.draft.b_powod_zgloszenia == "Kontrola leczenia."
        assert created.usage["estimated_cost_usd"] == pytest.approx(0.23)
        assert created.timings.transkrypcja is not None
        assert "równolegle" in created.timings.summary()

        stored = temp_db.get_visit(created.visit_id, doctor_id="lekarz-1")
        assert stored["raw_transcript"] == "Lekarz: Jak się Pan czuje?"
        assert json.loads(stored["ai_note_original_json"])["raw_transcript"] == stored["raw_transcript"]

    def test_transcription_failure_keeps_the_documentation(self, fake_models):
        """Lekarz nie może stracić dokumentacji przez błąd materiału pomocniczego."""
        _, state = fake_models
        state["fail"] = "transcribe"
        created = _create()
        assert created.draft.raw_transcript == ""
        assert created.draft.b_powod_zgloszenia == "Kontrola leczenia."
        assert services.TRANSCRIPTION_FAILED_NOTE in created.draft.do_weryfikacji
        assert created.usage["estimated_cost_usd"] == pytest.approx(0.20)

    def test_documentation_failure_is_raised(self, fake_models, temp_db):
        _, state = fake_models
        state["fail"] = "documentation"
        with pytest.raises(RuntimeError, match="dokumentacja padła"):
            _create()

    def test_speech_mismatch_is_flagged(self, fake_models):
        """Dwa modele, dwie oceny nagrania — sprzeczność ma trafić do lekarza, nie zniknąć."""
        _, state = fake_models
        state["sekcje"] = _sekcje(jakosc_nagrania="BRAK_MOWY", b_powod_zgloszenia="")
        created = _create()
        assert services.SPEECH_MISMATCH_NOTE in created.draft.do_weryfikacji

    def test_silent_recording_without_transcript_is_not_flagged(self, fake_models):
        _, state = fake_models
        state["sekcje"] = _sekcje(jakosc_nagrania="BRAK_MOWY", b_powod_zgloszenia="")
        state["transcript"] = ""
        assert _create().draft.do_weryfikacji == []

    def test_switch_off_uses_single_call(self, fake_models):
        calls, _ = fake_models
        created = _create(separate=False)
        assert calls == {"documentation": [True], "transcribe": 0}
        assert created.draft.raw_transcript == "transkrypcja z jednego wywołania"
        assert created.timings.transkrypcja is None


class TestNoSpeechMarker:
    def test_marker_becomes_empty_transcript(self, monkeypatch, tmp_path):
        class Response:
            text = f" {NO_SPEECH_MARKER}\n"
            usage_metadata = None
            model_version = None

        class Models:
            def generate_content(self, **kwargs):
                return Response()

        class Client:
            models = Models()

        monkeypatch.setattr(gemini_client, "_client", lambda: Client())
        audio = tmp_path / "cisza.wav"
        audio.write_bytes(b"RIFF")
        text, usage = gemini_client.transcribe(audio, mime_type="audio/wav")
        assert text == ""
        assert usage["model"] == config.TRANSCRIPTION_MODEL


# --- Logowanie Google ----------------------------------------------------------------


class TestAllowlist:
    def test_parses_toml_list_and_env_string(self):
        assert parse_allowed_emails(["Lekarz@Example.com ", ""]) == {"lekarz@example.com"}
        assert parse_allowed_emails("a@x.pl, B@x.pl") == {"a@x.pl", "b@x.pl"}
        assert parse_allowed_emails(None) == frozenset()

    def test_listed_and_verified_is_allowed(self):
        assert email_allowed("Lekarz@example.com", verified=True, allowed={"lekarz@example.com"})

    def test_unverified_address_is_rejected(self):
        """Niezweryfikowany adres można wpisać dowolny — to nie jest dowód tożsamości."""
        assert not email_allowed("lekarz@example.com", verified=False, allowed={"lekarz@example.com"})

    def test_unlisted_address_is_rejected(self):
        assert not email_allowed("ktos@gmail.com", verified=True, allowed={"lekarz@example.com"})

    def test_empty_list_allows_everyone(self):
        assert email_allowed("ktos@gmail.com", verified=False, allowed=frozenset())


class TestSecretsTemplate:
    def test_top_level_keys_are_not_swallowed_by_auth_tables(self):
        """Klucz pod nagłówkiem `[auth.google]` należy do tej tabeli i aplikacja go nie widzi."""
        data = tomllib.loads((ROOT / ".streamlit" / "secrets.toml.example").read_text(encoding="utf-8"))
        for key in ("DATABASE_URL", "GEMINI_MODEL", "SEPARATE_TRANSCRIPTION", "ALLOWED_EMAILS"):
            assert key in data, key
        assert data["SEPARATE_TRANSCRIPTION"] is True
        google = data["auth"]["google"]
        assert google["server_metadata_url"].startswith("https://accounts.google.com/")
        assert set(google) == {"client_id", "client_secret", "server_metadata_url"}
        assert "auth0" not in data["auth"]


# --- Myślenie: poziom dla Gemini 3+, budżet dla 2.x -----------------------------


class TestThinkingConfig:
    @pytest.mark.parametrize(
        "model, expected",
        [
            ("gemini-3.8-flash", True),
            ("gemini-3.5-flash-lite", True),
            ("publishers/google/models/gemini-3.8-flash", True),
            ("gemini-2.5-pro", False),
            ("gemini-2.5-flash", False),
            ("cos-zupelnie-innego", True),
        ],
    )
    def test_family_detection(self, model, expected):
        assert config.uses_thinking_level(model) is expected

    def test_gemini_3_gets_level_not_budget(self):
        """Gemini 3 odrzuca budżet — wysłanie go kończy się błędem po wysłaniu nagrania."""
        cfg = gemini_client._thinking_config("gemini-3.8-flash", level="medium", budget=8192)
        assert cfg.thinking_budget is None
        assert str(cfg.thinking_level).endswith("MEDIUM")

    def test_gemini_2_gets_budget_not_level(self):
        cfg = gemini_client._thinking_config("gemini-2.5-pro", level="medium", budget=0)
        assert cfg.thinking_level is None
        assert cfg.thinking_budget == 128

    def test_transcription_thinks_minimally(self, monkeypatch):
        monkeypatch.setattr(gemini_client, "TRANSCRIPTION_MODEL", "gemini-3.8-flash")
        cfg = gemini_client._transcription_config()
        assert str(cfg.thinking_config.thinking_level).endswith("MINIMAL")

    def test_default_model_is_priced(self):
        """Domyślny model musi mieć stawki — inaczej panel liczy koszt najdroższą znaną."""
        from src.pricing import is_priced

        assert is_priced(config.DEFAULT_MODEL)


class TestClientIsShared:
    def test_parallel_first_calls_build_one_client(self, monkeypatch):
        """Regresja: dwa klienty z wyścigu → jeden sprzątany przez GC w trakcie zapytania."""
        import threading
        import time as _time

        built = []

        def slow_build():
            _time.sleep(0.05)  # okno na wyścig, jak przy prawdziwym logowaniu do Vertex
            built.append(object())
            return built[-1]

        monkeypatch.setattr(gemini_client, "_CLIENT", None)
        monkeypatch.setattr(gemini_client, "_build_client", slow_build)
        results = []
        threads = [threading.Thread(target=lambda: results.append(gemini_client._client())) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(built) == 1
        assert all(r is built[0] for r in results)
