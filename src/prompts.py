"""Polecenia dla modelu.

Treść kliniczna — rola, zasady, struktura sekcji A–Q, sposób redagowania — żyje w
`prompts/dokumentacja.md`, a nie tutaj. To celowe: to tekst lekarza, nie programisty,
i ma dać się poprawić bez dotykania kodu. Ten moduł dokleja do niego wyłącznie reguły
techniczne: jak sekcje mapują się na pola odpowiedzi, co zrobić z niemym nagraniem
i jak traktować przykłady stylu.
"""

import json
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

Odpowiadasz wyłącznie w formacie JSON zgodnym ze schematem. Pola `a_dane_ogolne` … \
`q_podsumowanie_plan` odpowiadają sekcjom A–Q z punktu 3.

- Sekcję, której obszaru nie poruszono podczas konsultacji, zostaw PUSTĄ. Aplikacja sama \
oznaczy ją jako nieporuszoną. Nie wpisuj tam ogólników, zastrzeżeń ani zdań w rodzaju \
„brak danych".
- Sekcję omówioną częściowo wypełnij tym, co padło, a brakujące elementy oznacz wewnątrz \
niej zgodnie z punktem 5.
- `e_leki`: osobny wpis dla każdego leku, o którym rozmawiano. W `opis` uwzględnij punkty \
1–16 sekcji E w zakresie, w jakim padły w rozmowie.
- `j_bezpieczenstwo.status`: OBECNE, ZAPRZECZA albo NIE_OCENIANO. Wybierz ZAPRZECZA wyłącznie \
wtedy, gdy temat został poruszony i pacjent zaprzeczył. Jeśli nie pytano — NIE_OCENIANO.
- `j_bezpieczenstwo.pilne`: true, gdy nagranie wskazuje na aktualne zagrożenie życia lub zdrowia.
- `do_weryfikacji`: konkretne kwestie do sprawdzenia przez lekarza przed zatwierdzeniem — \
niejasne lub sprzeczne informacje, nieustalone dawki, niepewne nazwy leków, nieczytelne \
fragmenty. Każda jednym zdaniem, ze wskazaniem sekcji.
- Nagranie bez zrozumiałej mowy (cisza, szum, uszkodzenie): `jakosc_nagrania` = BRAK_MOWY, \
`raw_transcript` puste, wszystkie sekcje puste, `j_bezpieczenstwo.status` = NIE_OCENIANO. \
Nie twórz żadnej treści — pusta dokumentacja jest tu jedyną poprawną odpowiedzią.
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


def build_few_shot_block(examples: list[dict]) -> str:
    """Zatwierdzona dokumentacja tej osoby jako wzorzec stylu.

    Bez transkrypcji: styl jest w dokumencie, który lekarz poprawił i zatwierdził, a nie
    w zapisie rozmowy. Transkrypcja w promptcie to tokeny i czas generowania za darmo
    oraz kanał, którym treść jednej wizyty mogłaby trafić do drugiej.
    """
    documents = [doc for ex in examples if (doc := _document_of(ex.get("note_json", "")))]
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


def build_user_prompt(
    few_shot_examples: list[dict],
    transcript: str | None = None,
    *,
    visit_type: str | None = None,
) -> str:
    """Polecenie dla jednej wizyty. Z `transcript` — praca na tekście, bez — na nagraniu."""
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
        parts.append("Przygotuj dokumentację zgodnie z poleceniem i schematem.")
    else:
        parts.append(
            "### NAGRANIE KONSULTACJI\n"
            "W załączeniu nagranie audio konsultacji.\n"
            "1. Najpierw oceń, czy nagranie zawiera zrozumiałą mowę.\n"
            "2. Jeśli TAK — przepisz dosłowną transkrypcję do `raw_transcript`, a następnie "
            "przygotuj dokumentację na jej podstawie.\n"
            "3. Jeśli NIE — postąp zgodnie z zasadą o nagraniu bez zrozumiałej mowy."
        )
    return "\n".join(p for p in parts if p)
