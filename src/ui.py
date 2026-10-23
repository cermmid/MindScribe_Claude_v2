"""Komponenty UI: widok dokumentacji, ostrzeżenie o ryzyku, przycisk kopiowania."""

import json
import re

import streamlit as st
import streamlit.components.v1 as components

from .formatting import NIE_PORUSZONO, PILNE, RYZYKO_OPIS, visit_type_label
from .schemas import SEKCJE

_TITLES = {title for _, title in SEKCJE}

# Znaki, które markdown Streamlita zamieniłby w formatowanie. Dokumentacja to tekst
# kliniczny, nie markdown: gwiazdka albo podkreślnik w wypowiedzi pacjenta nie mogą
# zamienić się w pogrubienie, a „1." na początku linii — w listę z inną numeracją.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")


def _escape(text: str) -> str:
    """Tekst dosłownie, z zachowanymi łamaniami linii."""
    escaped = _MD_SPECIAL.sub(r"\\\1", text)
    return "  \n".join(escaped.splitlines())


def _split_sections(document: str) -> list[tuple[str | None, str]]:
    """Podziel dokument na (nagłówek, treść) po nagłówkach sekcji A–Q.

    Lekarz edytuje całość jako jeden tekst, więc mógł zmienić albo usunąć nagłówki.
    Wtedy nic nie dzielimy — pokazujemy całość jako jeden blok, zamiast zgubić treść.
    """
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in document.splitlines():
        if line.strip() in _TITLES:
            sections.append((line.strip(), []))
        else:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in sections if title or "\n".join(body).strip()]


def render_document(document: str) -> None:
    """Wyświetl dokumentację: nagłówki sekcji, treść dosłownie, nieporuszone przygaszone."""
    for title, body in _split_sections(document):
        if title:
            st.markdown(f"**{_escape(title)}**")
        if not body:
            continue
        if body == NIE_PORUSZONO:
            # Przygaszone, żeby wzrok przeskakiwał po obszarach, których wizyta nie objęła.
            st.caption(body)
            continue
        if body.startswith(PILNE):
            st.error(f"**{_escape(PILNE)}**")
            body = body[len(PILNE):].strip()
        st.markdown(_escape(body))


def render_risk_banner(ryzyko: str | None, *, pilne: bool = False) -> None:
    """Status ryzyka samobójczego nad dokumentacją — trzy stany, każdy wyraźnie inny."""
    status = (ryzyko or "NIE_OCENIANO").upper()
    label = RYZYKO_OPIS.get(status, RYZYKO_OPIS["NIE_OCENIANO"])
    if pilne:
        st.error(
            "🚨 **Fragment oznaczony do PILNEJ weryfikacji** — nagranie może wskazywać na "
            "aktualne zagrożenie życia lub zdrowia. Sprawdź sekcję J."
        )
    if status == "OBECNE":
        st.error(f"⚠️ **Myśli i zachowania samobójcze: {label}.**")
    elif status == "ZAPRZECZA":
        st.success(f"Myśli i zachowania samobójcze: **{label}**.")
    else:
        # Nie zielony: brak oceny to nie jest dobra wiadomość, tylko luka w wywiadzie.
        st.warning(f"Myśli i zachowania samobójcze: **{label}**.")


def render_review_items(items: list[str]) -> None:
    """Kwestie do sprawdzenia przed zatwierdzeniem (punkt 6 promptu klinicznego).

    Stoją NAD dokumentem i do niego nie trafiają — to zadania dla lekarza, nie treść
    dokumentacji medycznej.
    """
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return
    st.warning(
        f"**Do sprawdzenia przed zatwierdzeniem ({len(items)})**  \n"
        + "  \n".join(f"• {_escape(i)}" for i in items)
    )


def visit_caption(visit_type: str | None) -> None:
    if visit_type:
        st.caption(visit_type_label(visit_type))


def copy_button(text: str, *, label: str = "📋 Skopiuj wszystko", key: str = "copy") -> None:
    """Przycisk kopiujący `text` do schowka. Działa w iframe Streamlit (fallback execCommand)."""
    # `json.dumps` nie escapuje „</", a tekst siedzi wewnątrz <script>. Dokumentację
    # lekarz pisze teraz swobodnie, więc „</script>" w treści zamknąłby skrypt w pół.
    payload = json.dumps(text).replace("</", "<\\/")
    html = f"""
    <button id="{key}" style="
        background:#1f77b4;color:#fff;border:none;border-radius:6px;
        padding:8px 16px;font-size:14px;cursor:pointer;">{label}</button>
    <script>
    const btn = document.getElementById("{key}");
    const txt = {payload};
    btn.addEventListener("click", async () => {{
        const orig = btn.innerText;
        try {{
            await navigator.clipboard.writeText(txt);
        }} catch (e) {{
            const ta = document.createElement("textarea");
            ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.focus(); ta.select();
            try {{ document.execCommand("copy"); }} catch (e2) {{}}
            document.body.removeChild(ta);
        }}
        btn.innerText = "✅ Skopiowano!";
        setTimeout(() => {{ btn.innerText = orig; }}, 1500);
    }});
    </script>
    """
    _embed_html(html, height=48)


def _embed_html(html: str, *, height: int) -> None:
    """Osadź własny HTML z JavaScriptem.

    `components.v1.html` jest wycofywane — Streamlit zapowiedział jego usunięcie, a
    `requirements.txt` nie ogranicza wersji od góry, więc świeże wdrożenie może dostać
    wydanie, w którym go już nie ma. Wtedy cała strona padłaby na przycisku kopiowania.
    `st.iframe` jest następcą; stara funkcja zostaje tylko dla wersji sprzed niego.

    Ostrzeżenie z dokumentacji `st.iframe` (nie podawać niezaufanego HTML) tu nie
    dotyczy: szablon jest nasz, a jedyna zmienna treść — dokumentacja — siedzi w nim
    wyłącznie jako zakodowany literał JSON (patrz `copy_button`).
    """
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # pragma: no cover — starsze wydania Streamlita
        components.html(html, height=height)
