"""Kształt dokumentacji z konsultacji — odpowiedź modelu i wersja zatwierdzona.

Dwa modele, celowo rozdzielone:

- `DokumentacjaDraft` to `response_schema` dla Gemini: jedna notatka z wizyty, pisana
  ciągłym tekstem jak przez lekarza, plus pola, których aplikacja potrzebuje w postaci
  maszynowej (jakość nagrania, status ryzyka, lista do sprawdzenia). Opisy pól są częścią
  polecenia dla modelu, nie dokumentacją kodu — model czyta je przy generowaniu.
- `ZatwierdzonaDokumentacja` to to, co lekarz zatwierdził: jeden tekst, który sam
  poprawił, plus potwierdzony przez niego status ryzyka samobójczego.

**Kolejność pól ma znaczenie.** Gemini generuje pola w kolejności schematu. Transkrypcja
jest pierwsza, więc model pisze notatkę, mając ją już przed sobą. `do_weryfikacji` jest
ostatnia, więc powstaje po napisaniu całości — to jest kontrola kompletności z punktu 6.
"""

from enum import Enum

from pydantic import BaseModel, Field


class JakoscNagrania(str, Enum):
    DOBRA = "DOBRA"
    SLABA = "SLABA"
    BRAK_MOWY = "BRAK_MOWY"


class Ryzyko(str, Enum):
    """Trzy stany, nie dwa.

    Pierwsza wersja aplikacji miała tu OBECNE/NIEOBECNE i kazała wpisać NIEOBECNE,
    gdy temat nie padł albo nagranie było niezrozumiałe. W dokumentacji brak rozmowy
    o ryzyku wyglądał wtedy dokładnie jak jego zaprzeczenie. Prompt kliniczny wprost
    tego zabrania: „Nie zapisuj »bez myśli samobójczych«, jeżeli temat nie został
    poruszony".
    """

    OBECNE = "OBECNE"
    ZAPRZECZA = "ZAPRZECZA"
    NIE_OCENIANO = "NIE_OCENIANO"


class Bezpieczenstwo(BaseModel):
    status: Ryzyko = Field(
        description=(
            "OBECNE — pacjent zgłasza myśli, zamiary, plany lub zachowania samobójcze albo "
            "samouszkodzenia (także pośrednio). ZAPRZECZA — temat został poruszony i pacjent "
            "zaprzecza. NIE_OCENIANO — temat nie został poruszony albo nagranie nie pozwala "
            "go ocenić. Nigdy nie wybieraj ZAPRZECZA, jeśli o to nie pytano."
        )
    )
    pilne: bool = Field(
        default=False,
        description=(
            "true, gdy nagranie wskazuje na aktualne zagrożenie życia lub zdrowia i fragment "
            "wymaga pilnej weryfikacji przez lekarza."
        ),
    )


# Sekcje A–Q z wcześniejszego formatu odpowiedzi (każdy obszar w osobnym polu).
# Model już ich nie dostaje — notatka jest jednym tekstem. Lista zostaje wyłącznie po to,
# żeby szkice zapisane w starym formacie dało się dalej wyświetlić w historii.
SEKCJE: list[tuple[str, str]] = [
    ("a_dane_ogolne", "A. Dane ogólne i sytuacja życiowa pacjenta"),
    ("b_powod_zgloszenia", "B. Główny powód zgłoszenia się na wizytę"),
    ("c_aktualne_objawy", "C. Szczegółowy wywiad dotyczący aktualnych objawów"),
    ("d_historia_leczenia", "D. Historia dotychczasowego leczenia psychiatrycznego"),
    ("e_leki", "E. Szczegółowy wywiad dotyczący stosowanych leków"),
    (
        "f_rozwoj_funkcjonowanie",
        "F. Wcześniejsze i obecne trudności rozwojowe oraz funkcjonowanie",
    ),
    ("g_kontekst_zyciowy", "G. Sytuacja rodzinna, społeczna i zawodowa jako kontekst objawów"),
    ("h_obciazenie_praca", "H. Obciążenie, wyczerpanie i objawy związane z pracą"),
    ("i_objawy_depresyjne", "I. Objawy depresyjne, apatia i wycofanie"),
    ("j_bezpieczenstwo", "J. Myśli samobójcze i inne kwestie bezpieczeństwa"),
    ("k_objawy_psychotyczne", "K. Objawy psychotyczne i zaburzenia spostrzegania"),
    ("l_inne_medyczne", "L. Inne istotne informacje medyczne"),
    ("m_stan_psychiczny", "M. Stan psychiczny zaobserwowany przez lekarza"),
    ("n_omowienie_leczenia", "N. Omówienie leczenia podczas wizyty"),
    ("o_psychoterapia_zalecenia", "O. Psychoterapia, psychoedukacja i inne zalecenia"),
    ("p_zwolnienie", "P. Zwolnienie lekarskie i uzasadnienie ograniczenia zdolności do pracy"),
    ("q_podsumowanie_plan", "Q. Podsumowanie wizyty i plan dalszego postępowania"),
]


# Schemat składamy z trzech części, żeby z tych samych pól powstały dwa warianty
# odpowiedzi. Pydantic układa pola klas bazowych od OSTATNIEJ na liście dziedziczenia,
# więc `class X(_Tresc, _Transkrypcja, _Jakosc)` daje kolejność: jakość, transkrypcja,
# notatka. Kolejność pilnują testy — Gemini generuje pola dokładnie w tej kolejności.


class _Jakosc(BaseModel):
    jakosc_nagrania: JakoscNagrania = Field(
        description=(
            "Ocena użyteczności nagrania. BRAK_MOWY — w nagraniu nie ma zrozumiałej mowy "
            "(cisza, szum, nagranie uszkodzone lub nierozpoznawalne). SLABA — mowa częściowo "
            "niezrozumiała. DOBRA — mowa zrozumiała. Oceniaj uczciwie: jeśli nie słyszysz "
            "wyraźnie wypowiedzi, wpisz BRAK_MOWY i zostaw pozostałe pola puste."
        )
    )


class _Transkrypcja(BaseModel):
    raw_transcript: str = Field(
        description=(
            "Dosłowna transkrypcja tego, co faktycznie słychać w nagraniu, po polsku. "
            "Jeśli w nagraniu nie ma zrozumiałej mowy, zostaw to pole PUSTE. "
            "Pod żadnym pozorem nie wymyślaj treści ani nie przenoś jej z przykładów."
        )
    )


class _Tresc(BaseModel):
    notatka: str = Field(
        default="",
        description=(
            "Notatka z wizyty napisana tak, jak napisałby ją lekarz po przeprowadzonej "
            "konsultacji: ciągły tekst w akapitach, BEZ nagłówków, liter sekcji, wypunktowań "
            "i oznaczeń „nie poruszono”. Obszary z punktu 3 polecenia to lista kontrolna "
            "tego, co uwzględnić, a nie struktura tekstu. Obszarów, których nie poruszono, "
            "nie wymieniaj. Leki opisuj zdaniami, z dawkami, okresem przyjmowania, efektem "
            "i działaniami niepożądanymi, jeśli padły. Pusta, gdy w nagraniu nie ma mowy."
        ),
    )
    bezpieczenstwo: Bezpieczenstwo = Field(
        description=(
            "Status myśli i zachowań samobójczych na potrzeby aplikacji — niezależnie od tego, "
            "co o tym napisano w notatce."
        )
    )
    do_weryfikacji: list[str] = Field(
        default_factory=list,
        description=(
            "Konkretne punkty do sprawdzenia przez lekarza PRZED zatwierdzeniem: niejasne "
            "lub sprzeczne informacje, nieustalone dawki, niepewne nazwy leków, nieczytelne "
            "fragmenty nagrania. Każdy punkt jednym zdaniem. Pusta lista, gdy nie ma czego "
            "sprawdzać."
        ),
    )


class NotatkaWizyty(_Tresc, _Jakosc):
    """Odpowiedź modelu dokumentacji, gdy transkrypcję robi osobny model.

    Bez pola transkrypcji: to ona była największą częścią tekstu, który model pisał
    token po tokenie. Model nadal SŁYSZY całe nagranie — nie musi go tylko przepisywać.
    """


class DokumentacjaDraft(_Tresc, _Transkrypcja, _Jakosc):
    """Pełny szkic do weryfikacji, nigdy dokument ostateczny.

    W trybie jednego wywołania to jest odpowiedź modelu. W trybie osobnej transkrypcji
    aplikacja składa go z `NotatkaWizyty` i tekstu od modelu transkrypcji —
    reszta aplikacji (zapis, widoki, historia) widzi zawsze ten sam kształt.
    """

    @classmethod
    def from_parts(cls, notatka: "NotatkaWizyty", transkrypcja: str) -> "DokumentacjaDraft":
        return cls(**notatka.model_dump(), raw_transcript=(transkrypcja or "").strip())


class ZatwierdzonaDokumentacja(BaseModel):
    """Dokumentacja po weryfikacji: tekst poprawiony przez lekarza.

    Status ryzyka trzymamy osobno, choć jest też w tekście sekcji J — to jedyne pole,
    którego aplikacja potrzebuje w postaci maszynowej (ostrzeżenie w historii), więc
    lekarz potwierdza je jawnie, zamiast zdawać się na treść, którą mógł przeredagować.
    """

    dokument: str = Field(min_length=1)
    ryzyko: Ryzyko
    jakosc_nagrania: JakoscNagrania = JakoscNagrania.DOBRA
