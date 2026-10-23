"""Warstwa danych — SQLAlchemy Core, przenośna między SQLite a PostgreSQL.

Adres bazy bierze się z `DATABASE_URL` (patrz `src/config.py`); bez konfiguracji
używamy lokalnego SQLite, żeby development i testy działały bez serwera.

Dlaczego Core, a nie ORM: zapytania są proste, a schemat opisany metadanymi pozwala
SQLAlchemy wygenerować poprawny DDL dla obu dialektów. To samo API i te same testy
działają więc na SQLite i na Postgresie — rozjazd między nimi jest regresją.

Trzy decyzje warte zapamiętania:

1. `_conn()` zwraca **transakcję** (`engine.begin()`), a nie samo połączenie.
   `engine.connect()` w SQLAlchemy 2 NIE commituje przy wyjściu z `with`, choć
   `sqlite3` commitował — bez tego zapisy znikałyby po cichu, a odczyty działały.
2. `created_at` trzymamy jako **tekst ISO w UTC**, nie jako typ czasu. Format jest
   stały i sortuje się leksykograficznie poprawnie, a cała aplikacja już go tak
   czyta (`display_name`, panel właściciela). Przejście na `timestamptz` to osobny
   krok, do zrobienia razem z aktualizacją tych miejsc.
3. Grupowanie po dniu robimy **w Pythonie**, nie w SQL. `date()` istnieje w SQLite,
   ale w Postgresie `date` to typ, nie funkcja — grupowanie po stronie aplikacji
   omija ten rozjazd całkowicie i przy tej skali nic nie kosztuje.
"""

from __future__ import annotations

import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

from .config import DATABASE_URL, FEW_SHOT_LIMIT

metadata = sa.MetaData()

# SQLite wymaga INTEGER PRIMARY KEY dla autoinkrementacji, Postgres woli BIGINT.
_AutoId = sa.BigInteger().with_variant(sa.Integer, "sqlite")

visits = sa.Table(
    "visits",
    metadata,
    sa.Column("id", _AutoId, primary_key=True, autoincrement=True),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("audio_path", sa.Text),
    sa.Column("pipeline", sa.Text, nullable=False, server_default="multimodal"),
    sa.Column("raw_transcript", sa.Text),
    sa.Column("ai_note_original_json", sa.Text, nullable=False),
    sa.Column("doctor_note_corrected_json", sa.Text),
    sa.Column("status", sa.Text, nullable=False, server_default="draft"),
    # Klucz właściciela (stabilne `sub` od dostawcy tożsamości) i osobno nazwa
    # do wyświetlania. Jedna kolumna nie może pełnić obu ról: `sub` jest
    # nieczytelny, a nazwa bywa zmieniana i nie nadaje się na klucz.
    sa.Column("doctor_id", sa.Text),
    sa.Column("doctor_name", sa.Text),
    sa.Column("visit_label", sa.Text),
    sa.Column("visit_type", sa.Text),
    sa.Column("prompt_tokens", sa.BigInteger, server_default="0"),
    sa.Column("output_tokens", sa.BigInteger, server_default="0"),
    sa.Column("total_tokens", sa.BigInteger, server_default="0"),
    # Numeric, nie REAL: w Postgresie REAL to float4 i traciłby precyzję na kwotach.
    sa.Column("estimated_cost_usd", sa.Numeric(14, 6), server_default="0"),
    sa.Column("prompt_audio_tokens", sa.BigInteger, server_default="0"),
    sa.Column("audio_duration_seconds", sa.Float),
    # Ile trwało wygenerowanie notatki. Bez tej kolumny „trwa za długo" jest
    # nierozstrzygalne — a to jedyna miara, po której widać, czy strojenie pomogło.
    sa.Column("generation_seconds", sa.Float),
    # Model, który policzył tę wizytę, i czy znaliśmy jego cennik. Bez tego koszty
    # historyczne są nieprzypisywalne: po zmianie modelu nie da się odróżnić, które
    # wiersze wyceniono po którym cenniku, ani które są tylko górnym oszacowaniem.
    sa.Column("gemini_model", sa.Text),
    # `true`, nie `1` — Postgres odrzuca liczbę jako domyślną wartość kolumny boolean.
    sa.Column("pricing_known", sa.Boolean, server_default=sa.text("true")),
    # Każdy odczyt filtruje po właścicielu — bez tego indeksu skanowalibyśmy całość.
    sa.Index("ix_visits_owner", "doctor_id", "id"),
)

_engines: dict[str, sa.Engine] = {}


def get_engine() -> sa.Engine:
    """Silnik z pulą połączeń, jeden na adres bazy.

    Pula jest istotna dopiero przy Postgresie: obecny kod otwierał nowe połączenie
    na każde zapytanie, co przy bazie zdalnej oznacza pełny handshake za każdym razem.
    """
    url = DATABASE_URL
    if url not in _engines:
        kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if not url.startswith("sqlite"):
            kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
        engine = sa.create_engine(url, **kwargs)
        # Rejestrujemy PRZED tworzeniem schematu, żeby zagnieżdżone wywołania
        # `get_engine()` nie weszły w rekurencję.
        _engines[url] = engine
        _ensure_schema(engine)
    return _engines[url]


def reset_engine_cache() -> None:
    """Zamknij i wyrzuć silniki — używane przez testy przy podmianie bazy."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


class DatabaseUnavailable(RuntimeError):
    """Nie udało się połączyć z bazą — z podpowiedzią, co najczęściej jest przyczyną."""


_PASSWORD_IN_URL = re.compile(r"(://[^:/@]+:)[^@]*(@)")


def _scrub(text: str) -> str:
    """Usuń hasło z komunikatu — trafia on na ekran i do logów."""
    return _PASSWORD_IN_URL.sub(r"\1***\2", text)


@contextmanager
def _conn():
    """Transakcja commitowana przy wyjściu z bloku `with`.

    Świadomie `begin()`, nie `connect()` — patrz punkt 1 w docstringu modułu.

    Błędy połączenia zamieniamy na własny wyjątek z czytelnym opisem. Streamlit
    ukrywa treść nieobsłużonych wyjątków („original error message is redacted"),
    więc bez tego każdy problem z bazą wygląda tak samo i nie da się go zdiagnozować.
    """
    try:
        with get_engine().begin() as conn:
            yield conn
    except sa.exc.OperationalError as exc:
        raise _unavailable(exc) from exc


def _unavailable(exc: sa.exc.OperationalError) -> DatabaseUnavailable:
    return DatabaseUnavailable(
        "Nie udało się połączyć z bazą danych.\n\n"
        "Najczęstsze przyczyny:\n"
        "1. **Nieaktualne hasło w `DATABASE_URL`** — jeśli resetowałeś je w panelu bazy, "
        "connection string w sekretach trzeba podmienić.\n"
        "2. **Literówka albo obcięty adres** — sprawdź, czy skopiowałeś go w całości, "
        "razem z `?sslmode=require`.\n"
        "3. **Projekt bazy usunięty albo wstrzymany** w panelu dostawcy.\n\n"
        f"Komunikat sterownika: {_scrub(str(exc.orig or exc))}"
    )


def _row(row) -> dict[str, Any]:
    """Wiersz jako zwykły słownik, z typami, których spodziewa się reszta aplikacji.

    Postgres zwraca `Numeric` jako `Decimal`; aplikacja liczy na floatach, więc
    konwertujemy tutaj, zamiast łatać każde miejsce użycia z osobna.
    """
    data = dict(row._mapping)
    cost = data.get("estimated_cost_usd")
    if cost is not None:
        data["estimated_cost_usd"] = float(cost)
    return data


def init_db() -> None:
    """Publiczne wejście: upewnij się, że schemat istnieje i jest kompletny."""
    try:
        _ensure_schema(get_engine())
    except sa.exc.OperationalError as exc:
        raise _unavailable(exc) from exc


def _ensure_schema(engine: sa.Engine) -> None:
    """Utwórz schemat i dołóż brakujące kolumny w istniejącej bazie.

    Wołane przy tworzeniu silnika, więc **nie da się trafić do bazy przed
    inicjalizacją** — wcześniej wejście prosto pod adres podstrony omijało `init_db()`
    z `app.py` i przy Postgresie kończyłoby się „relation does not exist".

    `create_all` nie modyfikuje istniejących tabel, więc bazy założone przed
    dodaniem kolumn (np. sprzed `doctor_id`) domykamy jawnym ALTER-em. Introspekcja
    idzie przez inspektor SQLAlchemy, bo `PRAGMA table_info` istnieje tylko w SQLite.
    """
    metadata.create_all(engine)

    inspector = sa.inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("visits")}
    missing = [col for col in visits.columns if col.name not in existing]
    if not missing:
        return

    dialect = engine.dialect
    with engine.begin() as conn:
        for column in missing:
            ddl_type = column.type.compile(dialect)
            default = f" DEFAULT {column.server_default.arg}" if column.server_default else ""
            conn.execute(sa.text(f"ALTER TABLE visits ADD COLUMN {column.name} {ddl_type}{default}"))


def insert_visit(
    *,
    audio_path: str | None,
    pipeline: str,
    raw_transcript: str,
    ai_note_original_json: str,
    doctor_id: str,
    doctor_name: str | None = None,
    visit_label: str | None = None,
    visit_type: str | None = None,
    usage: dict | None = None,
    audio_duration_seconds: float | None = None,
    generation_seconds: float | None = None,
) -> int:
    usage = usage or {}
    stmt = (
        sa.insert(visits)
        .values(
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            audio_path=audio_path,
            pipeline=pipeline,
            raw_transcript=raw_transcript,
            ai_note_original_json=ai_note_original_json,
            status="draft",
            doctor_id=doctor_id,
            doctor_name=doctor_name,
            visit_label=visit_label,
            visit_type=visit_type,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            estimated_cost_usd=float(usage.get("estimated_cost_usd", 0.0)),
            # Bez rozbicia modalności liczba zawiera też prompt tekstowy — nie zapisujemy jej.
            prompt_audio_tokens=int(
                usage.get("prompt_audio_tokens", 0) if usage.get("modality_known") else 0
            ),
            audio_duration_seconds=audio_duration_seconds,
            generation_seconds=generation_seconds,
            gemini_model=usage.get("model") or None,
            pricing_known=bool(usage.get("pricing_known", True)),
        )
        # `lastrowid` nie działa pod psycopg — id musi wrócić z samego zapytania.
        .returning(visits.c.id)
    )
    with _conn() as conn:
        return conn.execute(stmt).scalar_one()


class _AllUsers:
    """Wartownik oznaczający ŚWIADOMY brak filtrowania po właścicielu."""

    def __repr__(self) -> str:  # pragma: no cover - tylko dla czytelności błędów
        return "ALL_USERS"


ALL_USERS = _AllUsers()
"""Jedyny sposób na pominięcie filtra właściciela — trzeba go podać jawnie."""


def _owner_clause(doctor_id: str | _AllUsers):
    """Warunek własności wiersza.

    Parametr jest **wymagany i zawodzi zamknięciem**. Wcześniej `None` znaczyło
    „bez filtrowania", więc zapomniany argument cicho pokazywał cudze wizyty.
    Teraz pominięcie to `TypeError` przy wywołaniu, a świadomy brak filtra wymaga
    jawnego `ALL_USERS` — czyli widać go w kodzie i w przeglądzie zmian.
    """
    if doctor_id is ALL_USERS:
        return sa.true()
    if not doctor_id:
        raise ValueError(
            "Brak identyfikatora właściciela. Podaj identyfikator użytkownika "
            "albo jawnie ALL_USERS, jeśli zapytanie ma naprawdę objąć wszystkich."
        )
    return visits.c.doctor_id == doctor_id


def update_visit(
    visit_id: int,
    *,
    doctor_note_corrected_json: str,
    status: str = "approved",
    doctor_id: str | _AllUsers,
) -> int:
    """Zapisz poprawioną notatkę. Zwraca liczbę zmienionych wierszy.

    Zwracany licznik ma znaczenie po włączeniu izolacji: aktualizacja cudzej wizyty
    nie rzuca błędu, tylko nie zmienia niczego — bez tej informacji wołający
    zameldowałby sukces mimo braku zapisu.
    """
    stmt = (
        sa.update(visits)
        .where(visits.c.id == visit_id, _owner_clause(doctor_id))
        .values(doctor_note_corrected_json=doctor_note_corrected_json, status=status)
    )
    with _conn() as conn:
        return conn.execute(stmt).rowcount


def get_visit(visit_id: int, *, doctor_id: str | _AllUsers) -> dict[str, Any] | None:
    stmt = sa.select(visits).where(visits.c.id == visit_id, _owner_clause(doctor_id))
    with _conn() as conn:
        row = conn.execute(stmt).first()
        return _row(row) if row else None


def list_visits(*, doctor_id: str | _AllUsers) -> list[dict[str, Any]]:
    stmt = (
        sa.select(
            visits.c.id,
            visits.c.created_at,
            visits.c.visit_label,
            visits.c.visit_type,
            visits.c.status,
            visits.c.pipeline,
            visits.c.doctor_id,
            visits.c.doctor_name,
            visits.c.prompt_tokens,
            visits.c.output_tokens,
            visits.c.total_tokens,
            visits.c.estimated_cost_usd,
        )
        .where(_owner_clause(doctor_id))
        .order_by(visits.c.id.desc())
    )
    with _conn() as conn:
        return [_row(r) for r in conn.execute(stmt)]


# --- Agregaty dla panelu właściciela ------------------------------------------
#
# UWAGA: te zapytania celowo NIE selektują `raw_transcript`, `ai_note_original_json`,
# `doctor_note_corrected_json` ani `visit_label`. Właściciel aplikacji nie jest osobą
# prowadzącą tych pacjentów, więc wgląd w treść wizyty byłby udostępnieniem
# dokumentacji medycznej osobie nieuprawnionej. Wyłącznie metadane i agregaty.

_UNKNOWN_OWNER = "(nieznany)"


def admin_user_stats() -> list[dict[str, Any]]:
    """Statystyki per użytkownik: liczba wizyt, czas, koszt, ostatnia aktywność."""
    owner = sa.func.coalesce(visits.c.doctor_id, _UNKNOWN_OWNER)
    stmt = (
        sa.select(
            owner.label("doctor_id"),
            sa.func.max(sa.func.coalesce(visits.c.doctor_name, owner)).label("doctor"),
            sa.func.count().label("visits"),
            sa.func.sum(sa.case((visits.c.status == "approved", 1), else_=0)).label("approved"),
            sa.func.coalesce(sa.func.sum(visits.c.audio_duration_seconds), 0.0).label(
                "measured_seconds"
            ),
            sa.func.coalesce(sa.func.sum(visits.c.prompt_audio_tokens), 0).label("audio_tokens"),
            sa.func.coalesce(sa.func.sum(visits.c.estimated_cost_usd), 0).label("cost_usd"),
            sa.func.max(visits.c.created_at).label("last_activity"),
        )
        .group_by(owner)
        .order_by(sa.func.count().desc())
    )
    with _conn() as conn:
        rows = []
        for row in conn.execute(stmt):
            data = dict(row._mapping)
            data["visits"] = int(data["visits"])
            data["approved"] = int(data["approved"] or 0)
            data["measured_seconds"] = float(data["measured_seconds"] or 0.0)
            data["audio_tokens"] = int(data["audio_tokens"] or 0)
            data["cost_usd"] = float(data["cost_usd"] or 0.0)
            rows.append(data)
        return rows


def admin_daily_stats() -> list[dict[str, Any]]:
    """Wizyty i koszty dzień po dniu — do wykresu w panelu.

    Grupowanie po dniu robimy w Pythonie: `date()` w SQLite jest funkcją, a w
    Postgresie `date` to typ, więc to samo zapytanie zachowałoby się inaczej.
    """
    stmt = sa.select(visits.c.created_at, visits.c.estimated_cost_usd)
    with _conn() as conn:
        rows = conn.execute(stmt).all()

    per_day: dict[str, dict[str, float]] = defaultdict(lambda: {"visits": 0, "cost_usd": 0.0})
    for created_at, cost in rows:
        day = str(created_at or "")[:10]
        per_day[day]["visits"] += 1
        per_day[day]["cost_usd"] += float(cost or 0.0)

    return [
        {"day": day, "visits": int(agg["visits"]), "cost_usd": agg["cost_usd"]}
        for day, agg in sorted(per_day.items())
    ]


def admin_visit_durations() -> list[dict[str, Any]]:
    """Per-wizyta: czas i koszt, bez żadnej treści. Do tabeli szczegółowej w panelu."""
    stmt = sa.select(
        visits.c.id,
        visits.c.created_at,
        sa.func.coalesce(visits.c.doctor_name, visits.c.doctor_id, _UNKNOWN_OWNER).label("doctor"),
        visits.c.visit_type,
        visits.c.status,
        visits.c.audio_duration_seconds,
        visits.c.generation_seconds,
        visits.c.prompt_audio_tokens,
        visits.c.estimated_cost_usd,
        visits.c.gemini_model,
        visits.c.pricing_known,
    ).order_by(visits.c.id.desc())
    with _conn() as conn:
        return [_row(r) for r in conn.execute(stmt)]


def get_approved_examples(
    *,
    doctor_id: str | _AllUsers,
    limit: int = FEW_SHOT_LIMIT,
) -> list[dict[str, str]]:
    """Ostatnie zatwierdzone dokumentacje tej osoby — wzorzec stylu dla modelu.

    Bez transkrypcji: przykład stylu to zatwierdzony dokument, nie zapis rozmowy.
    Transkrypcji nie pobieramy wcale, skoro nie trafia do promptu — dane pacjenta,
    których nie potrzebujemy, nie powinny w ogóle opuszczać bazy.
    """
    stmt = (
        sa.select(visits.c.doctor_note_corrected_json)
        .where(
            visits.c.status == "approved",
            visits.c.doctor_note_corrected_json.is_not(None),
            _owner_clause(doctor_id),
        )
        .order_by(visits.c.id.desc())
        .limit(limit)
    )
    with _conn() as conn:
        return [{"note_json": note_json} for (note_json,) in conn.execute(stmt)]
