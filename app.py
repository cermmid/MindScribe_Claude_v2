"""Punkt wejścia — logowanie, inicjalizacja bazy i nawigacja.

Nawigację budujemy jawnie przez `st.navigation`, a nie przez katalog `pages/`.
Powód jest prozaiczny: przy katalogu `pages/` Streamlit bierze nazwy pozycji
wprost z nazw plików, więc strona główna nazywała się „app". Tutaj każda pozycja
ma nazwę i ikonę ustawioną wprost.

Efekt uboczny jest cenniejszy niż sama nazwa: logowanie i przygotowanie bazy
dzieją się w JEDNYM miejscu, zanim uruchomi się jakikolwiek widok. Wcześniej
wejście prosto pod adres podstrony omijało oba te kroki.
"""

from pathlib import Path

import streamlit as st

from src.auth import require_login
from src.db import DatabaseUnavailable, init_db

# Ścieżka budowana od pliku, nie względna: `streamlit run` uruchomiony z innego
# katalogu roboczego nie znalazłby grafiki, a objawem byłby brak logo bez błędu.
_ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="MindScribe", page_icon="🧠", layout="wide")

# Dwie poprawki wyglądu. Obie celują w wewnętrzne atrybuty Streamlita
# (`data-stale`, `data-testid`), więc przy jego aktualizacji trzeba je sprawdzić —
# gdy selektor przestanie trafiać, nic się nie zepsuje, tylko wróci zachowanie domyślne.
#
# 1. Streamlit przygasza całą stronę przy każdym przeładowaniu skryptu, a przeładowanie
#    odpala każde kliknięcie — zaznaczenie opcji, wyjście z pola tekstowego. Miga to
#    bez przerwy i przy dłuższej pracy nad dokumentacją męczy wzrok. Wygaszamy przygaszanie;
#    o tym, że coś się liczy, i tak mówi pasek postępu przy generowaniu.
# 2. Logo powiększone ponad to, co pozwala `st.logo` — szczegóły przy regule niżej.
st.markdown(
    """
    <style>
    [data-stale="true"], .element-container[data-stale="true"],
    [data-testid="stAppViewContainer"] [data-stale="true"] {
        opacity: 1 !important;
        transition: none !important;
        filter: none !important;
    }

    /* Logo. `size="large"` w `st.logo` to najwięcej, co daje API — reszta musi
       pójść stylem. Skalujemy SZEROKOŚCIĄ, nie wysokością: przy `height` i
       `max-width` naraz przeglądarka potrafi zgnieść proporcje, a tak logo
       zawsze mieści się w panelu i nigdy się nie zniekształca.
       Odstępy nad i pod logo mają być RÓWNE, liczone do liter w menu:
         nad  = padding-top nagłówka (0.6) + margin-top logo (0.9)      = 1.5rem
         pod  = margin-bottom logo (1.2) + własny padding pozycji menu  ≈ 1.5rem
       Pozycja menu ma własne wypełnienie między krawędzią a tekstem, więc dolny
       margines jest o nie mniejszy — inaczej dół wyszedłby wizualnie większy.
       Gdy trzeba to przestroić, wystarczy ruszyć `margin-bottom` niżej.

       Selektor jest potrójny celowo: `stLogo` to nazwa, której Streamlit używa
       dzisiaj, a `stSidebarHeader img` łapie logo niezależnie od tej nazwy —
       w nagłówku panelu nie ma innego obrazka. Bez tego zapasowego trafienia
       zmiana nazwy w Streamlicie cicho przywraca domyślny rozmiar. */
    section[data-testid="stSidebar"] [data-testid="stLogo"],
    [data-testid="stSidebarHeader"] img,
    [data-testid="stLogo"] {
        display: block;
        /* Szerokość w pikselach, NIE w procentach. Procent liczy się od nagłówka
           panelu, a ten nie jest pełnej szerokości — stąd logo wychodziło mniejsze,
           niż zakładała reguła. `max-width` przy `height: auto` tylko zmniejsza
           proporcjonalnie, więc nic się nie zniekształci. */
        width: 220px !important;
        max-width: 100% !important;
        height: auto !important;
        max-height: none !important;
        margin: 0.9rem auto 1.2rem 0 !important;
    }
    /* Nagłówek panelu ma stałą wysokość i przyciąłby powiększone logo. */
    [data-testid="stSidebarHeader"] {
        height: auto !important;
        padding-top: 0.6rem;
        padding-bottom: 0;
    }
    /* Własne wypełnienie listy nawigacji dodałoby się do marginesu logo
       i rozjechało symetrię wyliczoną wyżej. */
    [data-testid="stSidebarNav"] {
        padding-top: 0 !important;
        margin-top: 0 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

require_login()

# PO `require_login`, nie przed: logowanie kończy się `st.stop()`, a na ekranie
# logowania nie ma jeszcze panelu bocznego — logo trafiłoby wtedy w lewy górny róg
# obszaru głównego i wyglądało jak przypadek. `st.logo` renderuje się nad całą
# zawartością panelu, więc ląduje też nad podpisem „Zalogowano" z `src/auth.py`.
st.logo(
    str(_ASSETS / "logo.svg"),
    icon_image=str(_ASSETS / "logo-icon.svg"),
    size="large",
)

try:
    init_db()
except DatabaseUnavailable as exc:
    st.error(str(exc))
    st.stop()

st.navigation(
    [
        # „Wizyta" jest pierwsza i otwiera się od razu: po to lekarz wchodzi do aplikacji.
        st.Page("views/new_visit.py", title="Wizyta", icon="🎙️", default=True),
        st.Page("views/history.py", title="Historia wizyt", icon="📚"),
        st.Page("views/home.py", title="O aplikacji", icon="ℹ️"),
    ]
).run()
