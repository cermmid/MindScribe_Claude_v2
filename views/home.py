import streamlit as st

st.title("🧠 MindScribe")
st.subheader("Asystent dokumentacji z konsultacji")

st.markdown(
    """
Nagraj konsultację i odzyskaj czas, który zwykle pochłania jej dokumentowanie.

MindScribe słucha nagrania i przygotowuje szczegółowy, uporządkowany zapis wizyty:
powód zgłoszenia, aktualne objawy, historię leczenia, osobny opis każdego leku,
omówione leczenie i zalecenia. Ty czytasz, poprawiasz i zatwierdzasz — ostatnie
słowo zawsze należy do Ciebie.

**Jak pracuje:**

- **Szczegółowo, nie lakonicznie.** Odtwarza przebieg wywiadu zamiast skracać go
  do streszczenia — nie gubi informacji istotnych klinicznie.
- **Wiernie nagraniu.** Zapisuje tylko to, co padło. Czego nie poruszono, oznacza jako
  nieporuszone — brak rozmowy o objawie nie staje się jego zaprzeczeniem.
- **Nie stawia rozpoznań i nie planuje leczenia.** Dokumentuje rozpoznania i zalecenia
  wyłącznie wtedy, gdy wypowiedział je lekarz.
- **Pokazuje, co sprawdzić.** Niejasne dawki, sprzeczne informacje i niepewne nazwy
  leków dostajesz jako listę do weryfikacji przed zatwierdzeniem.
- **Bezpieczeństwo na wierzchu.** Status myśli samobójczych widać od razu — także wtedy,
  gdy tematu nie poruszono, bo to też jest informacja.
- **Uczy się Twojego stylu.** Im więcej dokumentacji zatwierdzisz, tym bardziej kolejne
  przypominają sposób, w jaki piszesz Ty.

Zacznij od **Nowa wizyta** w panelu po lewej.
    """
)

st.info(
    "⚠️ Wersja testowa. Korzystaj wyłącznie z nagrań fikcyjnych lub odegranych — "
    "aplikacja nie jest jeszcze przygotowana do pracy z danymi prawdziwych pacjentów."
)
