"""Logowanie przez dostawcę OIDC (Auth0) — natywne `st.login()`.

Dlaczego nie własny formularz z hasłem: `st.session_state` żyje tylko w ramach
połączenia, więc odświeżenie strony albo wygaszony ekran telefonu wyrzucałyby
użytkownika z sesji. `st.login()` trzyma tożsamość w podpisanym ciasteczku, które
to przeżywa — a przy okazji nie przechowujemy żadnych haseł.

Rozdzielenie dwóch pojęć, które wcześniej pełniła jedna wartość:

- `current_user_id()` → `st.user.sub`, czyli **stabilny identyfikator** od dostawcy.
  To jest klucz w bazie. Nie e-mail — ten użytkownik może zmienić, a wtedy stracilibyśmy
  dostęp do własnych wizyt.
- `current_doctor()` → imię albo e-mail, **wyłącznie do wyświetlania** i do podpisu
  pod notatką. Nigdy jako klucz.

Wcześniej obie role pełniło ręcznie wpisywane imię przy wspólnym haśle — czyli
„Anna Kowalska" i „anna kowalska" były dwiema osobami, a każdy znający hasło mógł
wpisać cudze imię i przejąć jego wizyty.
"""

import streamlit as st

_PROVIDER = "auth0"


def _auth_configured() -> bool:
    """Czy w sekretach jest sekcja `[auth]` z dostawcą.

    Bez konfiguracji aplikacja działa dalej (dev lokalny, testy), ale bez tożsamości —
    i o tym trzeba powiedzieć wprost, zamiast po cichu wpuszczać wszystkich.
    """
    try:
        return bool(st.secrets.get("auth"))
    except Exception:
        return False


def current_user_id() -> str | None:
    """Stabilny identyfikator zalogowanej osoby. Klucz danych w bazie."""
    try:
        if st.user.is_logged_in:
            return st.user.sub
    except Exception:
        pass
    return None


def current_doctor() -> str | None:
    """Nazwa do wyświetlania — imię i nazwisko albo e-mail. Nigdy jako klucz."""
    try:
        if st.user.is_logged_in:
            return st.user.get("name") or st.user.get("email")
    except Exception:
        pass
    return None


def require_login() -> None:
    """Zatrzymaj stronę, dopóki użytkownik się nie zaloguje."""
    if not _auth_configured():
        st.error(
            "**Logowanie nie jest skonfigurowane.**\n\n"
            "Uzupełnij sekcje `[auth]` i `[auth.auth0]` w sekretach — patrz README. "
            "Bez tego aplikacja nie wie, kto z niej korzysta, więc nie może pokazać "
            "żadnych wizyt."
        )
        st.stop()

    if current_user_id():
        _render_sidebar()
        return

    st.title("🧠 MindScribe")
    st.subheader("Asystent specjalistów od zdrowia psychicznego")
    st.write(
        "Zaloguj się, żeby przejść do swoich wizyt. Swoją dokumentację widzisz wyłącznie Ty."
    )
    if st.button("Zaloguj się / Załóż konto", type="primary"):
        st.login(_PROVIDER)
    st.stop()


def _render_sidebar() -> None:
    if name := current_doctor():
        st.sidebar.caption(f"👤 Zalogowano: {name}")
    if st.sidebar.button("Wyloguj", use_container_width=True):
        st.logout()

