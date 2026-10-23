"""Logowanie kontem Google — natywne `st.login()`.

Dlaczego nie własny formularz z hasłem: `st.session_state` żyje tylko w ramach
połączenia, więc odświeżenie strony albo wygaszony ekran telefonu wyrzucałyby
użytkownika z sesji. `st.login()` trzyma tożsamość w podpisanym ciasteczku, które
to przeżywa — a przy okazji nie przechowujemy żadnych haseł.

Dlaczego Google, a nie Auth0: projekt Google Cloud i tak istnieje (Vertex AI), więc
klient logowania powstaje w tym samym miejscu. Jeden dostawca i jedna umowa mniej.

Rozdzielenie dwóch pojęć:

- `current_user_id()` → `st.user.sub`, czyli **stabilny identyfikator** od Google.
  To jest klucz w bazie. Nie e-mail — ten da się zmienić, a wtedy osoba straciłaby
  dostęp do własnych wizyt.
- `current_doctor()` → imię albo e-mail, **wyłącznie do wyświetlania**. Nigdy jako klucz.

**Kto może wejść.** Z Auth0 konto zakładało się samemu. Z Google zalogować się może
każdy, kto ma konto Google — a każda wizyta kosztuje i trafia do bazy. Dlatego jest
lista dozwolonych adresów (`ALLOWED_EMAILS`). Pusta lista wpuszcza wszystkich, tak
jak wcześniej; ustawiona — tylko wymienionych.
"""

import os

import streamlit as st

_PROVIDER = "google"


def _auth_configured() -> bool:
    """Czy w sekretach jest sekcja `[auth.google]`.

    Bez konfiguracji aplikacja nie wie, kto z niej korzysta — i o tym trzeba powiedzieć
    wprost, zamiast po cichu wpuszczać wszystkich.
    """
    try:
        return bool((st.secrets.get("auth") or {}).get(_PROVIDER))
    except Exception:
        return False


def parse_allowed_emails(raw) -> frozenset[str]:
    """Lista dozwolonych adresów z sekretów (lista TOML) albo ze zmiennej środowiskowej
    (adresy rozdzielone przecinkami). Wielkość liter nie ma znaczenia."""
    if not raw:
        return frozenset()
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    return frozenset(e.strip().lower() for e in items if str(e).strip())


def _allowed_emails() -> frozenset[str]:
    try:
        raw = st.secrets.get("ALLOWED_EMAILS")
    except Exception:
        raw = None
    return parse_allowed_emails(raw or os.getenv("ALLOWED_EMAILS"))


def email_allowed(email: str | None, *, verified, allowed: frozenset[str]) -> bool:
    """Czy ta osoba może korzystać z aplikacji.

    Przy ustawionej liście adres musi być na niej i być **potwierdzony** przez Google.
    Bez potwierdzenia ktoś mógłby podpiąć cudzy adres do własnego konta i wejść na
    listę cudzym nazwiskiem.
    """
    if not allowed:
        return True
    if verified is not True and str(verified).lower() != "true":
        return False
    return (email or "").strip().lower() in allowed


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
    """Zatrzymaj stronę, dopóki użytkownik się nie zaloguje i nie jest na liście."""
    if not _auth_configured():
        st.error(
            "**Logowanie nie jest skonfigurowane.**\n\n"
            "Uzupełnij sekcje `[auth]` i `[auth.google]` w sekretach — patrz README. "
            "Bez tego aplikacja nie wie, kto z niej korzysta, więc nie może pokazać "
            "żadnych wizyt."
        )
        st.stop()

    if current_user_id():
        if not email_allowed(
            st.user.get("email"),
            verified=st.user.get("email_verified"),
            allowed=_allowed_emails(),
        ):
            st.title("🧠 MindScribe")
            st.error(
                f"Konto **{st.user.get('email') or 'bez adresu e-mail'}** nie ma dostępu "
                "do tej aplikacji. Jeśli powinno, poproś właściciela aplikacji o dopisanie "
                "tego adresu."
            )
            if st.button("Wyloguj i zaloguj się innym kontem"):
                st.logout()
            st.stop()
        _render_sidebar()
        return

    st.title("🧠 MindScribe")
    st.subheader("Asystent dokumentacji z konsultacji")
    st.write(
        "Zaloguj się kontem Google, żeby przejść do swoich wizyt. "
        "Swoją dokumentację widzisz wyłącznie Ty."
    )
    if st.button("Zaloguj się przez Google", type="primary"):
        st.login(_PROVIDER)
    st.stop()


def _render_sidebar() -> None:
    if name := current_doctor():
        st.sidebar.caption(f"👤 Zalogowano: {name}")
    if st.sidebar.button("Wyloguj", use_container_width=True):
        st.logout()
