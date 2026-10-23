import streamlit as st

from src.audio import looks_silent
from src.auth import current_doctor, current_user_id
from src.db import DatabaseUnavailable, get_visit
from src.formatting import (
    RYZYKO_OPIS,
    audio_quality_label,
    audio_unusable,
    risk_is_urgent,
    risk_status,
    with_header,
)
from src.services import (
    DEFAULT_AUDIO_SUFFIX,
    VisitNotUpdated,
    approve_documentation,
    build_approved_documentation,
    create_visit_from_audio,
    derive_audio_suffix,
    load_few_shot_examples,
)
from src.ui import (
    copy_button,
    render_document,
    render_review_items,
    render_risk_banner,
    visit_caption,
)

st.title("🎙️ Nowa wizyta")

# --- 1. Dane wizyty ------------------------------------------------------------
st.header("1. Dane wizyty")
visit_label = st.text_input(
    "Etykieta wizyty (opcjonalnie)",
    placeholder="np. pacjent A — kontrola leczenia",
    help=(
        "NIE wpisuj imienia, nazwiska ani żadnych danych, które umożliwią identyfikację "
        "pacjenta przez osoby trzecie. Używaj pseudonimu lub krótkiego opisu."
    ),
)
visit_type = st.radio("Typ wizyty", ["Pierwsza", "Kolejna"], horizontal=True)

# --- 2. Wejście audio ----------------------------------------------------------
st.header("2. Wejście audio")
col_a, col_b = st.columns(2)
with col_a:
    recorded = st.audio_input("🎙️ Nagraj wizytę")
with col_b:
    uploaded = st.file_uploader(
        "…albo wgraj plik audio (mp3 / wav / m4a / ogg)",
        type=["mp3", "wav", "m4a", "ogg", "webm", "flac"],
    )

audio_bytes: bytes | None = None
audio_suffix = DEFAULT_AUDIO_SUFFIX
audio_mime: str | None = None
# Nagranie z mikrofonu ma pierwszeństwo — to główna ścieżka, upload jest alternatywą.
if recorded is not None:
    audio_bytes = recorded.getvalue()
    audio_mime = getattr(recorded, "type", None)
    audio_suffix = derive_audio_suffix(getattr(recorded, "name", None))
elif uploaded is not None:
    audio_bytes = uploaded.getvalue()
    audio_mime = getattr(uploaded, "type", None)
    audio_suffix = derive_audio_suffix(uploaded.name)

# --- 3. Generowanie dokumentacji -----------------------------------------------
st.header("3. Generowanie dokumentacji")

# Cisza w nagraniu = model nie ma czego transkrybować. Sprawdzamy PRZED wysyłką,
# żeby nie płacić za pustą dokumentację i żeby lekarz od razu wiedział, że to mikrofon.
_silent = audio_bytes is not None and looks_silent(audio_bytes, audio_mime)
_force = False
if _silent:
    st.error(
        "🔇 **W nagraniu nie ma dźwięku.** Przeglądarka nagrywała, ale nie dostała sygnału "
        "z mikrofonu — jeśli podczas nagrywania wykres się nie ruszał, to jest właśnie ta sytuacja.\n\n"
        "Najczęstsze przyczyny (po kolei):\n"
        "1. **Windows blokuje mikrofon przeglądarce** — Ustawienia → Prywatność i zabezpieczenia → "
        "Mikrofon → włącz *Zezwalaj aplikacjom klasycznym na dostęp do mikrofonu*. "
        "Uprawnienie w przeglądarce może być przyznane, a system i tak podaje ciszę.\n"
        "2. **Przeglądarka używa innego urządzenia** niż testowane w Windows — kliknij ikonę "
        "kłódki/mikrofonu przy adresie strony i wybierz właściwy mikrofon.\n"
        "3. **Inna aplikacja trzyma mikrofon** (Teams, Zoom, Discord) — zamknij ją i odśwież stronę."
    )
    _force = st.checkbox("Wyślij mimo to (jeśli uważasz, że nagranie jest dobre)")

if st.button(
    "🪄 Przygotuj dokumentację",
    type="primary",
    disabled=audio_bytes is None or (_silent and not _force),
):
    try:
        few_shot = load_few_shot_examples(current_user_id())
    except DatabaseUnavailable as exc:
        st.error(str(exc))
        st.stop()
    # Szczegółowa dokumentacja jest kilka razy dłuższa niż dawna notatka — lepiej
    # uprzedzić, niż zostawić lekarza z wrażeniem, że aplikacja się zawiesiła.
    _spinner_text = (
        "Słucham nagrania i przygotowuję szczegółową dokumentację… "
        "przy dłuższej wizycie to potrwa kilka minut."
    )
    if few_shot:
        _spinner_text += f" Styl na podstawie {len(few_shot)} wcześniej zatwierdzonych."
    with st.spinner(_spinner_text):
        try:
            created = create_visit_from_audio(
                audio_bytes,
                audio_suffix=audio_suffix,
                audio_mime=audio_mime,
                visit_label=visit_label,
                visit_type=visit_type,
                doctor_id=current_user_id(),
                doctor_name=current_doctor(),
                few_shot=few_shot,
            )
        except DatabaseUnavailable as exc:
            st.error(str(exc))
            st.stop()
        except Exception as e:
            st.error(f"Błąd wywołania Gemini: {e}")
            st.stop()

    # Nowa wizyta wypiera poprzednią zatwierdzoną z widoku — inaczej pod spodem wisiałaby
    # cudza dokumentacja i łatwo byłoby skopiować nie tę.
    for key in ("approved_doc", "approved_visit_id", "approved_visit_created_at", "approved_visit_type"):
        st.session_state.pop(key, None)
    st.session_state["current_visit_id"] = created.visit_id
    st.session_state["current_draft"] = created.draft.model_dump(mode="json")
    st.session_state["current_document"] = created.document
    st.session_state["current_visit_type"] = visit_type
    st.session_state["debug_prompt"] = created.debug_prompt
    st.success(
        f"Wizyta #{created.visit_id} zapisana jako szkic. "
        f"Gotowe w {created.timings.calosc:.0f} s."
    )
    # Rozbicie, a nie sama suma: gdy znów zrobi się wolno, od razu widać, który
    # etap urósł. Kosztów specjalista nadal nie widzi — to jest czas, nie pieniądze.
    st.caption(created.timings.summary())

# Zużycie tokenów i koszt są dalej zapisywane do bazy, ale świadomie NIE pokazywane
# lekarzowi — to dane biznesowe właściciela, widoczne w osobnym panelu (admin/app.py).

# --- 4. Weryfikacja i edycja ----------------------------------------------------
if "current_draft" in st.session_state:
    st.header("4. Weryfikacja i edycja")
    draft = st.session_state["current_draft"]
    visit_id = st.session_state["current_visit_id"]

    # Jeśli model nie miał czego słuchać, lekarz musi to zobaczyć PRZED czytaniem treści.
    if audio_unusable(draft):
        st.error(
            "🔇 **W nagraniu nie wykryto zrozumiałej mowy.** "
            "Dokumentacja poniżej jest pusta — to poprawne zachowanie, model ma zakaz "
            "tworzenia treści, której nie słyszał. Sprawdź mikrofon i nagraj wizytę ponownie."
        )
    elif warning := audio_quality_label(draft):
        st.warning(f"🔉 {warning}")

    _transcript = (draft.get("raw_transcript") or "").strip()
    if not _transcript and not audio_unusable(draft):
        st.warning(
            "Transkrypcja jest pusta, choć model nie zgłosił problemu z nagraniem. "
            "Zweryfikuj nagranie przed zatwierdzeniem dokumentacji."
        )

    _pilne = risk_is_urgent(draft)
    render_risk_banner(risk_status(draft), pilne=_pilne)

    # Punkt 6 promptu: istotne niejasności lekarz ma zobaczyć PRZED zatwierdzeniem.
    render_review_items(draft.get("do_weryfikacji") or [])

    with st.expander("📄 Transkrypcja nagrania (do porównania)", expanded=False):
        st.text_area(
            "Transkrypcja",
            value=_transcript,
            height=300,
            disabled=True,
            label_visibility="collapsed",
        )

    # Klucz związany z wizytą: nowe generowanie musi dać świeży tekst, a nie zostawić
    # w polu poprawki wniesione do poprzedniej wizyty.
    document = st.text_area(
        "Dokumentacja — popraw dowolny fragment przed zatwierdzeniem",
        value=st.session_state["current_document"],
        height=720,
        key=f"document_{visit_id}",
    )

    # Jedyny element poza tekstem. Status ryzyka jest w treści sekcji J, ale lekarz mógł
    # ją przeredagować — tu potwierdza go jawnie, bo aplikacja używa go do ostrzeżeń.
    _statuses = list(RYZYKO_OPIS)
    _default = risk_status(draft)
    ryzyko = st.radio(
        "🛑 Myśli i zachowania samobójcze — potwierdź",
        _statuses,
        index=_statuses.index(_default),
        format_func=lambda s: RYZYKO_OPIS[s],
        horizontal=True,
        key=f"ryzyko_{visit_id}",
    )

    _confirmed_urgent = True
    if _pilne:
        _confirmed_urgent = st.checkbox(
            "Zweryfikowałem/am fragment oznaczony jako pilny.",
            key=f"pilne_{visit_id}",
        )

    if st.button("✅ Zatwierdź i zapisz", type="primary", disabled=not _confirmed_urgent):
        try:
            approved = build_approved_documentation(
                dokument=document,
                ryzyko=ryzyko,
                jakosc_nagrania=draft.get("jakosc_nagrania") or "DOBRA",
            )
        except Exception as e:
            st.error(f"Dokumentacja nie przeszła walidacji: {e}")
            st.stop()

        try:
            approve_documentation(visit_id, approved, doctor_id=current_user_id())
        except VisitNotUpdated as e:
            # Zapis nie doszedł do skutku — lepiej powiedzieć to wprost, niż pokazać
            # „zatwierdzono" i pozwolić zamknąć kartę z niezapisaną dokumentacją.
            st.error(f"Dokumentacja NIE została zapisana. {e}")
            st.stop()

        st.session_state["approved_doc"] = approved.model_dump(mode="json")
        st.session_state["approved_visit_id"] = visit_id
        # Data z rekordu wizyty, nie z zegara: nagranie bywa wgrywane później niż
        # odbyta wizyta, więc „dzisiaj" bywa nieprawdą w dokumentacji.
        _record = get_visit(visit_id, doctor_id=current_user_id())
        st.session_state["approved_visit_created_at"] = (_record or {}).get("created_at")
        st.session_state["approved_visit_type"] = st.session_state.get("current_visit_type")
        st.success(
            f"Wizyta #{visit_id} zatwierdzona. Ta dokumentacja posłuży jako wzorzec stylu "
            "przy kolejnych wizytach."
        )
        for key in ("current_draft", "current_document", "current_visit_id", "debug_prompt"):
            st.session_state.pop(key, None)

    if "debug_prompt" in st.session_state:
        with st.expander("🐛 Debug: prompt wysłany do Gemini", expanded=False):
            st.code(st.session_state["debug_prompt"], language="markdown")

# --- 5. Zatwierdzona dokumentacja do skopiowania -------------------------------
if "approved_doc" in st.session_state:
    st.header("5. Gotowa dokumentacja")
    approved = st.session_state["approved_doc"]
    a_type = st.session_state.get("approved_visit_type")
    visit_caption(a_type)
    render_risk_banner(approved.get("ryzyko"))
    render_document(approved["dokument"])

    full_text = with_header(
        approved["dokument"],
        title=f"Wizyta #{st.session_state.get('approved_visit_id', '')}",
        visit_type=a_type,
        created_at=st.session_state.get("approved_visit_created_at"),
        doctor_name=current_doctor(),
    )
    with st.expander("📄 Pełny tekst (zaznacz i skopiuj ręcznie)", expanded=False):
        st.text(full_text)
    copy_button(full_text, key="copy_new")
