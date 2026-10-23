"""Kształt dokumentacji z konsultacji — odpowiedź modelu i wersja zatwierdzona.

Dwa modele, celowo rozdzielone:

- `DokumentacjaDraft` to `response_schema` dla Gemini. Sekcje A–Q odpowiadają punktowi 3
  promptu klinicznego (`prompts/dokumentacja.md`). Opisy pól są częścią polecenia dla
  modelu, nie dokumentacją kodu — model czyta je przy generowaniu.
- `ZatwierdzonaDokumentacja` to to, co lekarz zatwierdził: jeden tekst, który sam
  poprawił, plus potwierdzony przez niego status ryzyka samobójczego.

**Kolejność pól ma znaczenie.** Gemini generuje pola w kolejności schematu. Transkrypcja
jest pierwsza, więc model pisze sekcje, mając ją już przed sobą. `do_weryfikacji` jest
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


class Lek(BaseModel):
    nazwa: str = Field(
        description=(
            "Nazwa leku w takim brzmieniu, w jakim padła. Gdy pacjent nie jest pewien nazwy, "
            "dopisz to wprost, na przykład: sertralina (nazwa niepewna)."
        )
    )
    opis: str = Field(
        description=(
            "Szczegółowy opis tego leku według punktów 1–16 sekcji E, w zakresie, w jakim "
            "padły w rozmowie: dawki i ich zmiany, okres przyjmowania, powód, efekt, "
            "działania niepożądane, przyczyna i sposób odstawienia, przerwy, powrót. "
            "Pomiń punkty, o których nie rozmawiano — nie wypełniaj ich ogólnikami."
        )
    )


class Bezpieczenstwo(BaseModel):
    status: Ryzyko = Field(
        description=(
            "OBECNE — pacjent zgłasza myśli, zamiary, plany lub zachowania samobójcze albo "
            "samouszkodzenia (także pośrednio). ZAPRZECZA — temat został poruszony i pacjent "
            "zaprzecza. NIE_OCENIANO — temat nie został poruszony albo nagranie nie pozwala "
            "go ocenić. Nigdy nie wybieraj ZAPRZECZA, jeśli o to nie pytano."
        )
    )
    opis: str = Field(
        default="",
        description=(
            "Treść sekcji J: precyzyjnie, bez eufemizmów i bez bagatelizowania. Pusta, gdy "
            "temat nie został poruszony."
        ),
    )
    pilne: bool = Field(
        default=False,
        description=(
            "true, gdy nagranie wskazuje na aktualne zagrożenie życia lub zdrowia i fragment "
            "wymaga pilnej weryfikacji przez lekarza."
        ),
    )


# Jedno źródło prawdy o sekcjach: pole schematu → nagłówek w dokumencie. Używają go
# zarówno opisy pól poniżej, jak i składanie tekstu do edycji (`src/formatting.py`).
# Sekcje E (leki) i J (bezpieczeństwo) mają własną strukturę i są obsługiwane osobno.
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


def _sekcja(litera: str) -> str:
    return (
        f"Treść sekcji {litera} z punktu 3 polecenia. Zostaw PUSTE, jeśli tego obszaru nie "
        "poruszono — aplikacja sama oznaczy go jako nieporuszony. Nie wpisuj ogólników."
    )


class DokumentacjaDraft(BaseModel):
    """Odpowiedź modelu — szkic do weryfikacji, nigdy dokument ostateczny."""

    jakosc_nagrania: JakoscNagrania = Field(
        description=(
            "Ocena użyteczności nagrania. BRAK_MOWY — w nagraniu nie ma zrozumiałej mowy "
            "(cisza, szum, nagranie uszkodzone lub nierozpoznawalne). SLABA — mowa częściowo "
            "niezrozumiała. DOBRA — mowa zrozumiała. Oceniaj uczciwie: jeśli nie słyszysz "
            "wyraźnie wypowiedzi, wpisz BRAK_MOWY i zostaw pozostałe pola puste."
        )
    )
    raw_transcript: str = Field(
        description=(
            "Dosłowna transkrypcja tego, co faktycznie słychać w nagraniu, po polsku. "
            "Jeśli w nagraniu nie ma zrozumiałej mowy, zostaw to pole PUSTE. "
            "Pod żadnym pozorem nie wymyślaj treści ani nie przenoś jej z przykładów."
        )
    )
    a_dane_ogolne: str = Field(default="", description=_sekcja("A"))
    b_powod_zgloszenia: str = Field(default="", description=_sekcja("B"))
    c_aktualne_objawy: str = Field(default="", description=_sekcja("C"))
    d_historia_leczenia: str = Field(default="", description=_sekcja("D"))
    e_leki: list[Lek] = Field(
        default_factory=list,
        description=(
            "Sekcja E: osobny wpis dla KAŻDEGO leku, o którym rozmawiano — obecnego "
            "i wcześniejszego. Pusta lista, gdy o lekach nie rozmawiano."
        ),
    )
    f_rozwoj_funkcjonowanie: str = Field(default="", description=_sekcja("F"))
    g_kontekst_zyciowy: str = Field(default="", description=_sekcja("G"))
    h_obciazenie_praca: str = Field(default="", description=_sekcja("H"))
    i_objawy_depresyjne: str = Field(default="", description=_sekcja("I"))
    j_bezpieczenstwo: Bezpieczenstwo = Field(
        description="Sekcja J — myśli samobójcze i inne kwestie bezpieczeństwa."
    )
    k_objawy_psychotyczne: str = Field(default="", description=_sekcja("K"))
    l_inne_medyczne: str = Field(default="", description=_sekcja("L"))
    m_stan_psychiczny: str = Field(default="", description=_sekcja("M"))
    n_omowienie_leczenia: str = Field(default="", description=_sekcja("N"))
    o_psychoterapia_zalecenia: str = Field(default="", description=_sekcja("O"))
    p_zwolnienie: str = Field(default="", description=_sekcja("P"))
    q_podsumowanie_plan: str = Field(default="", description=_sekcja("Q"))
    do_weryfikacji: list[str] = Field(
        default_factory=list,
        description=(
            "Konkretne punkty do sprawdzenia przez lekarza PRZED zatwierdzeniem: niejasne "
            "lub sprzeczne informacje, nieustalone dawki, niepewne nazwy leków, nieczytelne "
            "fragmenty nagrania. Każdy punkt jednym zdaniem, ze wskazaniem sekcji. "
            "Pusta lista, gdy nie ma czego sprawdzać."
        ),
    )


class ZatwierdzonaDokumentacja(BaseModel):
    """Dokumentacja po weryfikacji: tekst poprawiony przez lekarza.

    Status ryzyka trzymamy osobno, choć jest też w tekście sekcji J — to jedyne pole,
    którego aplikacja potrzebuje w postaci maszynowej (ostrzeżenie w historii), więc
    lekarz potwierdza je jawnie, zamiast zdawać się na treść, którą mógł przeredagować.
    """

    dokument: str = Field(min_length=1)
    ryzyko: Ryzyko
    jakosc_nagrania: JakoscNagrania = JakoscNagrania.DOBRA
