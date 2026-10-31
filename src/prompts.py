"""Polecenia dla modelu.

Treść kliniczna — rola, zasady, zakres informacji A–Q, forma notatki — żyje w
`prompts/dokumentacja.md`, a nie tutaj. To celowe: to tekst lekarza, nie programisty,
i ma dać się poprawić bez dotykania kodu. Ten moduł dokleja do niego wyłącznie reguły
techniczne: jak notatka i pola pomocnicze mapują się na odpowiedź, co zrobić z niemym nagraniem
i jak traktować przykłady stylu.
"""

import json
import re
from pathlib import Path

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "dokumentacja.md"


def _load_clinical_prompt() -> str:
    text = _PROMPT_FILE.read_text(encoding="utf-8").strip()
    if not text:
        # Pusty plik dałby modelowi same reguły techniczne — dokumentację bez zasad
        # klinicznych. Lepiej nie wystartować niż wygenerować coś takiego.
        raise RuntimeError(f"Pusty prompt kliniczny: {_PROMPT_FILE}")
    return text


TECHNICAL_RULES = """\
8. Zasady techniczne aplikacji

Odpowiadasz wyłącznie w formacie JSON zgodnym ze schematem.

- `notatka`: cała notatka z wizyty jako jeden tekst, w formie z punktu 4 — akapity \
oddzielone pustą linią, bez nagłówków, liter A–Q, wypunktowań i znaczników formatowania. \
Tę treść lekarz wkleja wprost do dokumentacji medycznej.
- `bezpieczenstwo.status`: OBECNE, ZAPRZECZA albo NIE_OCENIANO. Wybierz ZAPRZECZA wyłącznie \
wtedy, gdy temat został poruszony i pacjent zaprzeczył. Jeśli nie pytano — NIE_OCENIANO, \
a w notatce o tym obszarze nie piszesz nic.
- `bezpieczenstwo.pilne`: true, gdy nagranie wskazuje na aktualne zagrożenie życia lub zdrowia.
- `do_weryfikacji`: konkretne kwestie do sprawdzenia przez lekarza przed zatwierdzeniem — \
niejasne lub sprzeczne informacje, nieustalone dawki, niepewne nazwy leków, nieczytelne \
fragmenty. Każda jednym zdaniem. Nie powtarzaj ich w notatce jako osobnej listy.
- Nagranie bez zrozumiałej mowy (cisza, szum, uszkodzenie): `jakosc_nagrania` = BRAK_MOWY, \
`notatka` pusta, `bezpieczenstwo.status` = NIE_OCENIANO. Nie twórz żadnej treści — \
pusta notatka jest tu jedyną poprawną odpowiedzią.
- PRZYKŁADY zatwierdzonej dokumentacji, jeśli są dołączone, pokazują wyłącznie sposób \
pisania tej osoby. Nigdy nie przenoś z nich treści, objawów, leków ani dawek.
"""

SYSTEM_PROMPT = _load_clinical_prompt() + "\n\n" + TECHNICAL_RULES


def _document_of(note_json: str) -> str:
    """Tekst zatwierdzonej dokumentacji z zapisu w bazie; pusty, gdy zapis jest w innym formacie."""
    try:
        data = json.loads(note_json or "")
    except (TypeError, ValueError):
        return ""
    return str(data.get("dokument") or "").strip() if isinstance(data, dict) else ""


# Nagłówek sekcji ze starego formatu: „A. Dane ogólne…", „J. Myśli samobójcze…".
_SECTION_HEADER = re.compile(r"^[A-Q]\. \S", re.MULTILINE)


def _is_sectioned(document: str) -> bool:
    """Dokumentacja zatwierdzona jeszcze w formacie sekcji A–Q.

    Taki przykład jako wzorzec stylu ciągnąłby model z powrotem do sekcji — a styl
    z przykładów model przejmuje silniej niż opis formy w poleceniu.
    """
    return len(_SECTION_HEADER.findall(document)) >= 3


def build_few_shot_block(examples: list[dict]) -> str:
    """Zatwierdzona dokumentacja tej osoby jako wzorzec stylu.

    Bez transkrypcji: styl jest w dokumencie, który lekarz poprawił i zatwierdził, a nie
    w zapisie rozmowy. Transkrypcja w promptcie to tokeny i czas generowania za darmo
    oraz kanał, którym treść jednej wizyty mogłaby trafić do drugiej.
    """
    documents = [
        doc
        for ex in examples
        if (doc := _document_of(ex.get("note_json", ""))) and not _is_sectioned(doc)
    ]
    if not documents:
        return ""

    blocks = [
        "### PRZYKŁADY ZATWIERDZONEJ DOKUMENTACJI TEJ OSOBY\n"
        "To dokumentacja z INNYCH, wcześniejszych wizyt, poprawiona przez lekarza. Pokazuje\n"
        "wyłącznie SPOSÓB PISANIA — długość zdań, dobór słów, poziom szczegółowości.\n"
        "Nie jest materiałem źródłowym: nie przenoś z niej żadnej treści.\n"
    ]
    for i, doc in enumerate(documents, 1):
        blocks.append(f"--- PRZYKŁAD {i} (inna wizyta, tylko wzorzec stylu) ---\n{doc}\n")
    blocks.append("--- KONIEC PRZYKŁADÓW ---\n")
    return "\n".join(blocks)


TRANSCRIPTION_PROMPT = """\
Przepisz dosłownie rozmowę z załączonego nagrania konsultacji lekarskiej, po polsku.

- Zapisuj wyłącznie to, co faktycznie słychać. Nie streszczaj, nie poprawiaj sensu, \
nie uzupełniaj niedosłyszanych fragmentów.
- Każdą wypowiedź zaczynaj od nowej linii, od oznaczenia mówiącego: „Lekarz:" albo \
„Pacjent:". Gdy nie da się rozpoznać, kto mówi, użyj „Osoba 1:" i „Osoba 2:".
- Fragment niezrozumiały oznacz jako [niezrozumiałe], zamiast zgadywać — szczególnie \
nazwy leków i dawki.
- Jeśli w nagraniu nie ma zrozumiałej mowy, odpowiedz jednym słowem: [BRAK MOWY].
- Odpowiedz samym tekstem transkrypcji, bez wstępu i bez komentarza.
"""

# Znacznik, którym model transkrypcji zgłasza nieme nagranie. Zamieniamy go na pusty
# tekst — w aplikacji „brak transkrypcji" ma jedną postać, a nie dwie.
NO_SPEECH_MARKER = "[BRAK MOWY]"


def build_user_prompt(
    few_shot_examples: list[dict],
    transcript: str | None = None,
    *,
    visit_type: str | None = None,
    with_transcript: bool = True,
) -> str:
    """Polecenie dla jednej wizyty. Z `transcript` — praca na tekście, bez — na nagraniu.

    `with_transcript=False`: transkrypcję robi osobny model, więc ten ma tylko słuchać
    i pisać dokumentację. Wtedy schemat odpowiedzi nie ma pola `raw_transcript`
    i polecenie nie może o nie prosić.
    """
    parts = [build_few_shot_block(few_shot_examples)]

    # Typ wizyty zmienia sens sekcji Q („zmiany od poprzedniej wizyty") i D. Bez tej
    # informacji model musi zgadywać, czy brak historii to luka, czy pierwsza wizyta.
    kind = (visit_type or "").strip().lower()
    if kind.startswith("pierw"):
        parts.append("### TYP WIZYTY\nPierwsza wizyta u tego lekarza.\n")
    elif kind.startswith("kolej"):
        parts.append("### TYP WIZYTY\nWizyta kolejna — pacjent jest już leczony u tego lekarza.\n")

    if transcript:
        parts.append(f"### TRANSKRYPCJA KONSULTACJI\n{transcript}\n")
        parts.append("Napisz notatkę z wizyty zgodnie z poleceniem i schematem.")
    elif with_transcript:
        parts.append(
            "### NAGRANIE KONSULTACJI\n"
            "W załączeniu nagranie audio konsultacji.\n"
            "1. Najpierw oceń, czy nagranie zawiera zrozumiałą mowę.\n"
            "2. Jeśli TAK — przepisz dosłowną transkrypcję do `raw_transcript`, a następnie "
            "napisz notatkę z wizyty na jej podstawie.\n"
            "3. Jeśli NIE — `raw_transcript` zostaw puste i postąp zgodnie z zasadą "
            "o nagraniu bez zrozumiałej mowy."
        )
    else:
        parts.append(
            "### NAGRANIE KONSULTACJI\n"
            "W załączeniu nagranie audio konsultacji. Transkrypcję przygotowuje osobny "
            "proces — Ty jej nie przepisujesz.\n"
            "1. Najpierw oceń, czy nagranie zawiera zrozumiałą mowę.\n"
            "2. Jeśli TAK — wysłuchaj całego nagrania uważnie i napisz notatkę z wizyty "
            "bezpośrednio na jego podstawie.\n"
            "3. Jeśli NIE — postąp zgodnie z zasadą o nagraniu bez zrozumiałej mowy."
        )
    return "\n".join(p for p in parts if p)
