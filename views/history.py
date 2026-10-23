import json

import streamlit as st

from src.auth import current_user_id
from src.db import DatabaseUnavailable, get_visit, list_visits
from src.formatting import display_name, humanize_visits_df, risk_is_urgent, with_header
from src.services import resolve_note_version
from src.ui import (
    copy_button,
    render_document,
    render_review_items,
    render_risk_banner,
    visit_caption,
)

st.title("📚 Historia wizyt")

try:
    visits = list_visits(doctor_id=current_user_id())
except DatabaseUnavailable as exc:
    st.error(str(exc))
    st.stop()
if not visits:
    st.info(
        "Brak zapisanych wizyt. Przejdź do **Nowa wizyta**, żeby przygotować pierwszą "
        "dokumentację."
    )
    st.stop()

# Koszty i zużycie tokenów są celowo poza widokiem lekarza — patrz admin/app.py.
st.metric("Wizyt łącznie", len(visits))

st.dataframe(
    humanize_visits_df(visits),
    use_container_width=True,
    hide_index=True,
)

_by_id = {v["id"]: v for v in visits}
selected_id = st.selectbox(
    "Wybierz wizytę do podglądu",
    options=[v["id"] for v in visits],
    format_func=lambda i: display_name(_by_id[i]),
)

with st.expander("📂 Pokaż szczegóły wizyty", expanded=False):
    visit = get_visit(selected_id, doctor_id=current_user_id())
    if not visit:
        st.warning("Nie znaleziono wizyty.")
    else:
        st.subheader(display_name(visit))
        st.caption(
            f"Utworzono: {visit['created_at']} · status: **{visit['status']}** · "
            f"tryb: {visit['pipeline']} · prowadzący: {visit.get('doctor_name') or '_brak_'}"
        )

        resolved = resolve_note_version(visit)
        full_text = ""
        if resolved.document is None:
            # Zapis w formacie, którego ta wersja nie składa — pokazujemy surowo,
            # zamiast udawać, że dokumentacja jest pusta.
            st.warning("Nie udało się odczytać dokumentacji tej wizyty — poniżej zapis źródłowy.")
            st.code(resolved.source_json or "_brak_")
        else:
            if not resolved.is_approved:
                st.info(
                    "Wizyta niezatwierdzona — pokazany szkic przygotowany przez asystenta, "
                    "przed weryfikacją lekarza."
                )
            visit_caption(visit.get("visit_type"))
            _draft = resolved.draft or {}
            render_risk_banner(
                resolved.ryzyko,
                pilne=risk_is_urgent(_draft) and not resolved.is_approved,
            )
            if not resolved.is_approved:
                render_review_items(_draft.get("do_weryfikacji") or [])
            render_document(resolved.document)

            full_text = with_header(
                resolved.document,
                title=display_name(visit),
                visit_type=visit.get("visit_type"),
                created_at=visit.get("created_at"),
                doctor_name=visit.get("doctor_name"),
            )
            with st.expander("📄 Pełny tekst (zaznacz i skopiuj ręcznie)", expanded=False):
                st.text(full_text)

        with st.expander("📄 Transkrypcja nagrania", expanded=False):
            st.text(visit.get("raw_transcript") or "brak")

        with st.expander("🔬 Surowy JSON (debug)", expanded=False):
            for label, key in (
                ("Szkic asystenta:", "ai_note_original_json"),
                ("Wersja zatwierdzona:", "doctor_note_corrected_json"),
            ):
                if raw := visit.get(key):
                    st.caption(label)
                    try:
                        st.json(json.loads(raw))
                    except ValueError:
                        st.code(raw)

        # Na samym dole, pod zwijanymi sekcjami — tam, gdzie lekarz kończy czytać.
        if full_text:
            copy_button(full_text, key=f"copy_hist_{selected_id}")
