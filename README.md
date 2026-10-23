# MindScribe — wersja druga

> **Czym jest to repo.** Druga wersja aplikacji, odbita z pełną historią z
> [`cermmid/MindScribe`](https://github.com/cermmid/MindScribe). Pierwsza wersja **działa i zostaje
> nietknięta** — jest używana na prawdziwych wizytach, więc nie eksperymentujemy na niej.
>
> ⚠️ **Ta wersja musi mieć własną bazę i własne logowanie.** To nie jest zalecenie porządkowe,
> tylko warunek bezpieczeństwa danych — szczegóły w sekcji *Rozdzielenie od pierwszej wersji*.

Aplikacja webowa dla **psychiatrów, psychologów i psychoterapeutów**: nagranie wizyty → transkrypcja i ustrukturyzowana notatka (Gemini 2.5 Flash, Structured Outputs) → korekta i zatwierdzenie przez specjalistę → zapis w bazie.

Zatwierdzone notatki są doklejane do kolejnych promptów jako **few-shot examples** (3 ostatnie), więc model uczy się stylu konkretnej osoby. Każdy widzi wyłącznie swoje wizyty.

## Stack

- **UI + backend**: Python 3.11+, Streamlit (multi-page)
- **AI**: Gemini 2.5 Flash przez `google-genai` SDK, Structured Outputs (Pydantic → `response_schema`)
- **Audio**: przesyłane inline i analizowane multimodalnie (transkrypcja + notatka w jednym wywołaniu). Files API odpada — nie istnieje w Vertex AI
- **Rozpoznania**: ICD-11 potwierdzane w oficjalnym rejestrze WHO; ICD-10 i DSM-5 przyjmowane bez odpytywania (patrz niżej)
- **Baza**: PostgreSQL przez SQLAlchemy (lokalnie SQLite bez konfiguracji)
- **Logowanie**: Auth0 przez natywne `st.login()`

## Uruchomienie lokalne

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# uzupełnij klucz Gemini oraz sekcje [auth] i [auth.auth0] — patrz niżej

streamlit run app.py
```

Aplikacja otworzy się w przeglądarce. Po zalogowaniu w panelu po lewej trzy pozycje: **Strona główna**, **Nowa wizyta** i **Historia wizyt**.

Panel właściciela (koszty, statystyki) to osobna aplikacja: `streamlit run admin/app.py`.

## Struktura

```
MindScribe/
├── app.py                    # router: logowanie, init bazy, nawigacja
├── views/
│   ├── home.py               # strona główna
│   ├── new_visit.py          # nagranie → generacja → korekta → zapis
│   └── history.py            # własne wizyty
├── src/
│   ├── config.py             # .env + stałe
│   ├── schemas.py            # PsychiatricNote (Pydantic, response_schema dla Gemini)
│   ├── prompts.py            # system prompt + builder few-shot
│   ├── db.py                 # SQLAlchemy: visits, filtr właściciela, agregaty panelu
│   ├── services.py           # przypadki użycia — bez zależności od UI
│   ├── auth.py               # logowanie OIDC, tożsamość użytkownika
│   ├── icd.py                # weryfikacja rozpoznań w rejestrze WHO
│   ├── audio.py              # zapis uploadu, typ MIME, wykrywanie ciszy
│   └── gemini_client.py      # generate_note_from_audio() — audio inline + Structured Output
├── admin/app.py              # panel właściciela (osobne hasło)
├── tests/                    # 115 testów; db też na Postgresie przez TEST_DATABASE_URL
└── data/                     # lokalny SQLite i nagrania (gitignored)
```

## Rozdzielenie od pierwszej wersji

Ta wersja **nie może dzielić bazy ani logowania** z `cermmid/MindScribe`. Trzy powody, każdy wystarczający sam z siebie:

1. **Wspólna baza znaczy pisanie do dokumentacji prawdziwych pacjentów.** Pierwsza wersja jest używana na realnych wizytach. Nieudana migracja schematu albo błąd w `update_visit` uderzyłby w notatki, które już istnieją — a tu nie ma kosza, do którego dałoby się sięgnąć.
2. **Wspólna baza otwiera z powrotem wyciek zamknięty w fazie B.** `load_few_shot_examples` dokleja zatwierdzone notatki do kolejnych promptów. Przy wspólnej bazie notatki z jednej wersji trafiałyby do zapytań drugiej — czyli transkrypcja pacjenta jednej osoby do promptu drugiej.
3. **Wspólne `cookie_secret` krzyżowo podpisuje sesje.** Dwie aplikacje pod różnymi adresami, ten sam podpis ciasteczka — sesja z jednej bywa uznawana przez drugą.

Co ta wersja musi mieć **własne**:

| Zasób | Dlaczego osobny |
|---|---|
| Projekt Neon (region UE) i `DATABASE_URL` | Patrz punkty 1 i 2 wyżej |
| Aplikacja Auth0 z własnym adresem powrotu | Inny adres aplikacji = inny `redirect_uri` |
| `cookie_secret` | Patrz punkt 3 |
| Aplikacja na Streamlit Cloud (inny adres) | Inaczej nadpisałaby pierwszą wersję |
| `admin_password` do panelu właściciela | Osobne dane, osobny dostęp |

Co **można współdzielić**: poświadczenia Gemini/Vertex AI (`GEMINI_API_KEY` albo service account) i klucze WHO ICD (`ICD_CLIENT_ID`, `ICD_CLIENT_SECRET`). To usługi zewnętrzne odpytywane tylko do odczytu — nie trzymają danych pacjentów i nie ma w nich stanu, który jedna wersja mogłaby zepsuć drugiej.

⚠️ Bez `DATABASE_URL` aplikacja domyślnie używa lokalnego SQLite w `data/`. Na Streamlit Cloud ten plik znika przy każdym restarcie — czyli *wygląda* na działającą i po cichu gubi wizyty. Ustaw `DATABASE_URL` od pierwszego uruchomienia.

## Deploy (Streamlit Community Cloud)

Cel: wysyłasz link, a odbiorca zakłada konto i korzysta — nic nie instaluje. Darmowe, jeśli repo jest publiczne.

### Setup jednorazowy (~5 min)

1. Upewnij się, że repo `cermmid/MindScribe_Claude_v2` jest **public** na GitHubie i kod jest na branchu `main`.
2. Wejdź na **https://share.streamlit.io** → zaloguj się kontem GitHub → kliknij **Create app** → **Deploy a public app from GitHub**.
3. Wskaż:
   - Repository: `cermmid/MindScribe_Claude_v2`
   - Branch: `main`
   - Main file path: `app.py`
   - App URL: **inny niż pierwsza wersja**, np. `mindscribe-v2` → dostaniesz `https://mindscribe-v2.streamlit.app`
4. **Advanced settings → Secrets** — wklej (wartości weź ze swojego `.streamlit/secrets.toml`):
   ```toml
   GEMINI_API_KEY = "..."
   GEMINI_MODEL = "gemini-2.5-pro"
   GEMINI_THINKING_BUDGET = 8192
   # logowanie: patrz sekcja "Logowanie i separacja danych"
   ```
5. **Deploy**. Po ~2 minutach apka żyje. Wysyłasz lekarzowi:
   > Link: https://mindscribe-v2.streamlit.app
   > Konto zakładasz sam przy pierwszym wejściu.

Każdy push do tego brancha automatycznie redeployuje apkę.

### Ograniczenia darmowego hostingu — przeczytaj zanim wyślesz link

- **Nagrania znikają przy restarcie.** Streamlit Cloud ma efemeryczny dysk. Wizyty i konta są bezpieczne, bo baza jest zewnętrzna (patrz *Baza danych*), ale pliki audio z `data/audio/` kasują się przy redeployu. Nagranie i tak jest potrzebne wyłącznie na czas wygenerowania notatki.
- **URL jest publiczny**, ale bez konta nie widać niczego — logowanie idzie przez Auth0.
- **~1 GB RAM, 1 CPU**. Wystarczy kilku osobom i plikom do ~200 MB.
- **Brak BAA z Google**. ZERO realnych danych pacjentów na tym deploy'u. Fikcyjne nagrania tylko.

## Logowanie i separacja danych

Każdy specjalista ma własne konto i **widzi wyłącznie swoje wizyty**. Logowanie idzie przez Auth0 natywnym `st.login()`, więc aplikacja nie przechowuje żadnych haseł, a sesja żyje w podpisanym ciasteczku — odświeżenie strony ani wygaszony ekran telefonu nie wylogowują.

### Konfiguracja Auth0 (10 min)

1. Konto na auth0.com → **Applications** → **Create Application** → typ **Regular Web Application**. Technologia „Python" (nie React ani inny SPA — te nie dostają Client Secret, a Streamlit go potrzebuje).
2. W *Settings* aplikacji uzupełnij:
   - **Allowed Callback URLs** — każdy adres, pod którym aplikacja działa, z końcówką `/oauth2callback`. To lista, więc wpisz oba: lokalny i produkcyjny.
     ```
     http://localhost:8501/oauth2callback, https://TWOJA-APKA.streamlit.app/oauth2callback
     ```
   - **Allowed Logout URLs** — te same adresy, ale **bez** `/oauth2callback`.
3. Przepisz `Domain`, `Client ID` i `Client Secret` do sekretów (patrz `.streamlit/secrets.toml.example`). `server_metadata_url` to `https://TWOJA-DOMENA/.well-known/openid-configuration`.
4. Wygeneruj `cookie_secret`: `python -c "import secrets; print(secrets.token_hex(32))"`.

⚠️ `redirect_uri` w sekretach musi **dokładnie** odpowiadać adresowi wpisanemu w Auth0. Lokalnie i na produkcji są to dwa różne adresy — na Streamlit Cloud w sekretach wpisz ten produkcyjny.

### Jak działa separacja

Kluczem właściciela jest `st.user.sub` — stabilny identyfikator od dostawcy, nie e-mail (ten użytkownik może zmienić, tracąc dostęp do własnych wizyt). Nazwa do wyświetlania jest w osobnej kolumnie `doctor_name`.

**Filtr właściciela zawodzi zamknięciem.** Parametr jest wymagany we wszystkich funkcjach czytających i piszących; pominięcie go to `TypeError`, a nie ciche „pokaż wszystko". Świadome zapytanie o wszystkich wymaga jawnego `ALL_USERS` — widocznego w kodzie i w przeglądzie zmian. Wcześniej domyślne `None` znaczyło „bez filtrowania", więc jedna zapomniana linia pokazywała cudze wizyty i nic tego nie sygnalizowało.

Najważniejsze miejsce to few-shot: bez filtra zatwierdzone notatki wszystkich trafiałyby do promptu, czyli **transkrypcja pacjenta jednej osoby lądowałaby w zapytaniu drugiej**.

Panel właściciela (`admin/app.py`) celowo widzi wszystkich — to jedyne miejsce bez filtra, chronione osobnym hasłem i ograniczone do metadanych.

## Baza danych

Warstwa danych stoi na SQLAlchemy Core i jest przenośna między SQLite a PostgreSQL. Adres bierze się z `DATABASE_URL`:

- **puste** → lokalny SQLite w `data/mindscribe.db` (development i testy działają bez serwera),
- **connection string z Neona** → PostgreSQL. Można go wkleić dokładnie tak, jak podaje panel — prefiks `postgresql://` jest zamieniany na `postgresql+psycopg://` automatycznie.

⚠️ Na Streamlit Community Cloud SQLite **znika przy każdym restarcie kontenera**, razem z wizytami. Trwała baza to warunek wieloosobowej pracy, nie ulepszenie.

### Sprawdzenie migracji na własnej bazie

Ta sama seria testów działa na obu silnikach — to jest dowód, że przejście na Postgresa niczego nie zmieniło:

```bash
pytest                                                   # SQLite
TEST_DATABASE_URL='postgresql://...' pytest              # PostgreSQL
```

⚠️ Testy **czyszczą tabelę `visits`** w podanej bazie. Podawaj wyłącznie bazę testową. Dlatego jest to osobna zmienna niż `DATABASE_URL` — żeby nie dało się wyczyścić produkcji przez pomyłkę.

### Rzeczy, które przy tej migracji psują się po cichu

Warto o nich wiedzieć, bo żadna nie rzuca błędu:

- `engine.connect()` w SQLAlchemy 2 **nie commituje** przy wyjściu z `with`, choć `sqlite3` commitował. Dlatego `_conn()` zwraca `engine.begin()` — inaczej zapisy znikałyby, a odczyty działały.
- `cur.lastrowid` nie działa pod psycopg — `insert_visit` używa `INSERT ... RETURNING id`.
- `date(created_at)` istnieje w SQLite, ale w Postgresie `date` to typ, nie funkcja. Grupowanie po dniu robimy w Pythonie.
- `REAL` w Postgresie to float4 — koszt trzymamy jako `Numeric(14,6)`.
- `SUM()` po kolumnie całkowitej zwraca `Decimal`; konwersja jest w jednym miejscu, przy odczycie wiersza.

## Weryfikacja rozpoznań w rejestrze WHO

**Model językowy nie może być źródłem kodów rozpoznań.** W testach podał kody ICD-11, które *istnieją*, ale znaczą co innego niż twierdził — np. `QE80` („ofiara przestępstwa lub terroryzmu") opisane jako zaburzenia snu, czy `6A70` (pojedynczy epizod depresyjny) podane jako lęk uogólniony. Walidacja formatu tego nie wykryje, bo kody są prawdziwe. ICD-11 obowiązuje dopiero od 2022 i jest w danych treningowych nieporównanie słabiej reprezentowana niż ICD-10, więc mapowanie kod ↔ znaczenie jest u modelu niepewne.

**ICD-10 to jednak inna historia.** Ta klasyfikacja jest w danych treningowych od dekad i model radzi sobie z nią dobrze — w praktyce odpytywanie rejestru dawało tu fałszywe „nie znaleziono" zamiast pomagać. Dlatego **ICD-10 przyjmujemy od modelu bez sprawdzania** (`VERIFY_ICD10=true` przywraca), a rejestr odpytujemy dla **ICD-11**, gdzie model demonstracyjnie się myli.

Wpisy mają trzy stany, nie dwa — „nie sprawdzano" to co innego niż „sprawdzono i nie ma":

| Stan | Znaczenie | Oznaczenie |
|---|---|---|
| `POTWIERDZONY` | zgodny z rejestrem WHO | ✅ |
| `NIESPRAWDZANY` | ICD-10 i DSM-5 — rejestru nie pytaliśmy | • |
| `NIEPOTWIERDZONY` | pytaliśmy i nie znaleziono | ❓ + ostrzeżenie |

Dla ICD-11 kody przechodzą przez oficjalne API WHO:

1. model podaje przede wszystkim **nazwę rozpoznania**, a kod tylko gdy jest go pewien,
2. aplikacja potwierdza kod w rejestrze i **zastępuje opis oficjalnym tytułem WHO**, więc para kod–opis nie może się rozjechać,
3. gdy kodu brak lub nie istnieje — szuka po nazwie rozpoznania,
4. czego nie da się potwierdzić, trafia do notatki oznaczone jako **niezweryfikowane**, także w tekście do skopiowania (`[DO WERYFIKACJI]`).

### Konfiguracja (5 min)

1. Załóż konto na https://icd.who.int/icdapi (darmowe).
2. Zakładka **API Access Keys** → **Create new key**. Skopiuj `Client Id` i `Client Secret`.
3. **Sprawdź klucze zanim wrzucisz je do aplikacji:**
   ```bash
   ICD_CLIENT_ID=... ICD_CLIENT_SECRET=... python3 scripts/test_icd.py
   ```
   Skrypt pobiera token, wyszukuje rozpoznanie w ICD-11 i sprawdza kilka konkretnych kodów w ICD-11 i ICD-10. Po każdym kroku pisze, co poszło nie tak — dzięki temu wiadomo, czy problem jest w kluczach, w sieci, czy w samym API.
4. Gdy skrypt pokazuje same ✅, wpisz `ICD_CLIENT_ID` i `ICD_CLIENT_SECRET` do sekretów aplikacji i zrestartuj ją.

Bez tych kluczy aplikacja **działa normalnie**, ale każde rozpoznanie jest oznaczane jako niezweryfikowane — awaria API czy brak konfiguracji nigdy nie przerywa generowania notatki.

⚠️ Weryfikacja potwierdza, że **kod istnieje i co oznacza**. Nie potwierdza, że rozpoznanie jest trafne klinicznie — to zawsze decyzja lekarza.

### Wybór klasyfikacji: ICD-10, ICD-11, DSM-5

Lekarz zaznacza jedną albo kilka naraz — przy kilku to samo rozpoznanie dostaje osobny wpis w każdym systemie, a widok i tekst do skopiowania grupują je po systemie.

**DSM-5 działa inaczej i trzeba o tym wiedzieć.** Wydaje go Amerykańskie Towarzystwo Psychiatryczne, jest objęty prawem autorskim i **nie ma publicznego rejestru do odpytania** — odpowiednika API WHO po prostu nie ma. Dlatego rozpoznania DSM-5 **zawsze** wracają oznaczone do weryfikacji, niezależnie od tego, jak pewny jest model.

Ponieważ DSM-5 posługuje się kodami ICD-10-CM, gdy model poda kod, robimy pomocniczą kontrolę w ICD-10 i dopisujemy jej wynik w uwadze („Kontrolnie: F41.1 w ICD-10 to…"). To wskazówka dla lekarza, **nie potwierdzenie** — ICD-10-CM to amerykańska modyfikacja i nie każdy jej kod istnieje w wersji WHO.

## Model i koszty

Domyślnie **`gemini-2.5-pro`** z budżetem myślenia **8192**. Przeszliśmy na niego z Flasha, bo przy długiej wizycie Flash mylił objawy aktualne z tymi, którym pacjent zaprzeczył albo które ustąpiły.

Trzy pokrętła zmieniasz w sekretach, bez ruszania kodu (wymagają restartu aplikacji):

| Zmienna | Domyślnie | Do czego |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-pro` | Zmiana modelu. Nieznany model jest wyceniany najdroższą znaną stawką i oznaczany w panelu. |
| `GEMINI_THINKING_BUDGET` | `8192` | Tyle wynosi domyślna wartość Google dla 2.5 Pro. Niżej = taniej i gorzej; `-1` = model decyduje sam, ale koszt przestaje być przewidywalny. Pro nie umie wyłączyć myślenia — wartości poniżej 128 są podnoszone do 128. |
| `FEW_SHOT_LIMIT` | `3` | Ile zatwierdzonych notatek trafia do promptu jako wzorzec stylu. `0` je wyłącza — użyj, gdyby model zaczął przenosić treść ze starych wizyt do nowej notatki. |

Orientacyjny koszt wizyty 45-minutowej (~86 tys. tokenów audio): **2.5 Flash ≈ $0,10**, **2.5 Pro z myśleniem 8192 ≈ $0,23**. Tokeny myślenia są rozliczane jak wyjście, więc to one, a nie audio, odpowiadają za różnicę.

⚠️ **Stawki w `src/pricing.py` pochodzą ze źródeł wtórnych i wymagają potwierdzenia** w konsoli Google Cloud. Cała tabela do poprawienia siedzi w jednym bloku na górze pliku; po sprawdzeniu ustaw tam `PRICES_ARE_PROVISIONAL = False`, żeby zniknęła informacja w panelu.

⚠️ **Modele Pro mają podwójną stawkę powyżej 200 tys. tokenów promptu** — to około 1 h 44 min nagrania. Aplikacja obsługuje ten próg, ale warto o nim wiedzieć, planując długość wizyt.

## Panel właściciela (koszty i statystyki)

Lekarze **nie widzą** kosztów ani zużycia tokenów — te dane są dalej zbierane i zapisywane, ale pokazywane wyłącznie w osobnej aplikacji:

```bash
streamlit run admin/app.py
```

Panel wymaga sekretu `admin_password` — osobnego od logowania specjalistów — i pokazuje:

- podsumowanie: liczba lekarzy, wizyt, łączny czas nagrań, koszt w PLN i średni koszt wizyty,
- tabelę per lekarz: liczba wizyt, łączny i średni czas, koszt, ostatnia aktywność,
- wykresy dzienne: liczba wizyt i koszt,
- listę wizyt pojedynczo — **wyłącznie metadane**.

### Czego panel nie pokazuje i dlaczego

Zapytania w `admin_user_stats()`, `admin_daily_stats()` i `admin_visit_durations()` celowo **nie selektują** `raw_transcript`, `ai_note_original_json`, `doctor_note_corrected_json` ani `visit_label`.

Właściciel aplikacji nie jest lekarzem prowadzącym tych pacjentów — wgląd w treść wizyty byłby udostępnieniem dokumentacji medycznej osobie nieuprawnionej. Liczba wizyt, czas trwania i koszt to dane operacyjne i te są w porządku. Nie usuwaj tego ograniczenia bez konsultacji z IOD.

### Skąd bierze się „czas wizyty"

Kolejność źródeł: **zmierzony** czas nagrania (kolumna `audio_duration_seconds`), a gdy go brak — **szacunek** z liczby tokenów audio (Gemini tokenizuje audio ze stałą częstotliwością ~32 tokeny/s).

Szacunek jest oznaczony w panelu i celowo **nie pojawia się dla nagrań krótszych niż 5 minut**: narzut tekstowy promptu (system prompt, schemat, few-shot) to 1–3 tys. tokenów, co przy krótkim nagraniu dominuje wynik. Przy realnej 30-minutowej wizycie ten sam narzut to kilka procent i jest do przyjęcia.

Aplikacja mobilna (PWA) będzie znała dokładny czas nagrywania, więc z czasem szacunek przestanie być potrzebny.

⚠️ **Panel jako osobna aplikacja wymaga wspólnej bazy.** Przy obecnym SQLite na dysku kontenera druga aplikacja na Streamlit Cloud dostanie własny, pusty plik. Lokalnie działa od razu; wdrożenie osobno ma sens dopiero po migracji na Postgresa.

## Włączenie Vertex AI (10–15 min)

Vertex AI w Google Cloud nie wykorzystuje danych klienta do trenowania modeli publicznych — w przeciwieństwie do darmowego tieru Gemini API. To pierwszy krok w stronę użycia z realnymi pacjentami (ale nie jedyny — patrz checklista poniżej).

1. **Google Cloud Console** → projekt z włączonym billingiem (ten sam, z którego masz klucz API).
2. **APIs & Services → Library** → wyszukaj „Vertex AI API" → **Enable**.
3. **IAM & Admin → Service Accounts** → **Create Service Account**. Nazwa np. `mindscribe-vertex`.
4. Tej service account przypisz rolę **Vertex AI User** (`roles/aiplatform.user`).
5. Otwórz utworzone konto → zakładka **Keys** → **Add key → Create new key → JSON** → pobierze się plik.
6. W Streamlit Cloud → *Manage app → Settings → Secrets* dopisz (oprócz istniejących):
   ```toml
   USE_VERTEX_AI = true
   GCP_PROJECT_ID = "twoj-projekt-id"
   GCP_LOCATION = "europe-west4"    # region UE; fallback: "europe-west1"

   GOOGLE_APPLICATION_CREDENTIALS_JSON = """
   <<wklej tutaj CAŁY plik JSON service account, bez zmian>>
   """
   ```
   Potrójne cudzysłowy są obowiązkowe — JSON ma własne cudzysłowy i znaki nowej linii.
7. **Reboot** apki. W Cloud Console → **Vertex AI → Metrics** powinieneś zobaczyć request po pierwszej generacji notatki (a NIE w „Generative Language API").

Stawki na Vertex AI bywają minimalnie inne niż na Gemini API; szacunek z `src/pricing.py` zostaje przybliżony.

## Zanim wpuścisz REALNEGO pacjenta — checklista RODO/medyczna

**Samo przełączenie klienta na Vertex AI to NIE wystarcza** do legalnego przetwarzania danych psychiatrycznych w UE. Dane o zdrowiu psychicznym to dane szczególnej kategorii (art. 9 RODO).

- [ ] **Umowa powierzenia przetwarzania (DPA)** podpisana z Google Cloud (Data Processing and Security Terms — robisz to w Cloud Console po stronie organizacji).
- [ ] **Hosting przeniesiony ze Streamlit Community Cloud na Google Cloud Run** w regionie UE (Streamlit Cloud nie ma DPA dla danych medycznych, a filesystem jest efemeryczny).
- [ ] **Anonimizacja PII przed wysłaniem do Gemini** (`src/anonymize.py` — TODO; usuwanie imion, nazwisk, PESEL, adresów, telefonów z transkrypcji przed promptem).
- [ ] **Zgoda pacjenta** na nagrywanie i analizę AI (formularz w UI lub papierowy; konkretna, świadoma, dobrowolna).
- [ ] **Audyt logów dostępu**, RBAC i bezpieczne uwierzytelnianie lekarzy (zamiast jednego hasła w sekretach).
- [ ] **Polityka retencji** nagrań i transkrypcji, prawo pacjenta do usunięcia (RODO art. 17).
- [ ] **Trwała baza** poza efemerycznym kontenerem (np. Postgres w UE).

Do tego momentu apka **testowana jest wyłącznie na fikcyjnych/zaaranżowanych nagraniach**.

## Roadmapa (poza MVP)

- Migracja hostingu na **Cloud Run w UE** + DPA z Google.
- `src/anonymize.py` — usuwanie PII z transkrypcji.
- Formularz zgody pacjenta przed nagraniem.
- Trwała baza Postgres (Supabase/Neon/Cloud SQL w UE).
- Multi-tenant — `doctor_id` realnie wykorzystany do separacji danych.
- Autentykacja per lekarz (zamiast jednego hasła), RBAC, audyt logów.
- Ścieżka B: Whisper → tekst → Gemini (alternatywny pipeline, `generate_note_from_text` już jest jako stub).
