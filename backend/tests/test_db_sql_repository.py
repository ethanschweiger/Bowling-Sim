"""`SqlAlchemyGameSessionRepository`: the first concrete, SQL-backed
`GameSessionRepository`. Offline only, like every other test in this
persistence-infrastructure arc -- no test here opens a real database
connection.

There is a real technical reason this file doesn't use a genuine SQLite
(or any other) engine: `app.db.schema.game_sessions.payload` is
PostgreSQL `JSONB`, which has no SQLite-compatible DDL rendering --
confirmed directly, before writing any of this, by running
`metadata.create_all()` against a real in-memory SQLite engine and
getting `CompileError: ... can't render element of type JSONB`. Genuine
round-trip execution against the real schema needs a real PostgreSQL
server, which this milestone explicitly doesn't add (no Docker Postgres
service, no live database anywhere in this suite).

`_FakeSession` below is the substitute: a small, stateful in-memory
stand-in scoped exactly to the three statement shapes
`app.db.row_store` produces (a plain `SELECT`, an
`ON CONFLICT DO UPDATE` upsert, an `ON CONFLICT DO NOTHING`
insert-if-absent) -- not a general SQL engine. It distinguishes the two
`INSERT`-shaped statements by compiling each to real PostgreSQL SQL text
(the same technique `test_db_row_store.py` already uses to assert on
that same text) and checking for `DO NOTHING` vs `DO UPDATE`, and reads
each statement's real bound parameters via `stmt.compile().params` --
so this exercises the adapter against the statements' genuine shape and
values, not a hand-waved mock that only records "was I called."
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.sql_repository import SqlAlchemyGameSessionRepository
from app.games.record_payload import GameSessionPayloadError
from app.games.service import GameService, InMemoryGameSessionRepository
from app.physics.ball import BALL_CATALOG
from app.physics.pinfall import PinfallResult
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw

BALL = BALL_CATALOG["house_ball"]
THROW = Throw()


def _scripted_throw(session, pins_knocked, fallen_pin_ids):
    """Same pattern used throughout this persistence arc's other test
    files -- a real trajectory (for real lane wear) with a scripted
    pinfall result."""

    def resolve_pinfall(_sim_result, _standing_ids):
        return PinfallResult(
            pins_knocked=pins_knocked,
            model_id="test-scripted",
            limitations="",
            fallen_pin_ids=tuple(fallen_pin_ids),
        )

    return session.throw(
        simulate=lambda condition: simulate_throw(BALL, THROW, condition),
        resolve_pinfall=resolve_pinfall,
    )


class _FakeResult:
    """Stands in for a SQLAlchemy `Result` -- just enough of
    `.mappings().one_or_none()`/`.mappings().one()` for the adapter's
    own usage."""

    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row

    def one(self):
        if self._row is None:
            raise sa.exc.NoResultFound("no row for the given game_id")
        return self._row


class _FakeSession:
    """See this module's own docstring for why this exists instead of a
    real engine/connection. `rows` is a plain dict shared across every
    session a `_FakeSessionFactory` produces, so state persists between
    calls the way a real database's stored rows would -- while
    committed/rolled_back/closed are tracked per-session, the way a
    real Session's own lifecycle is.
    """

    def __init__(self, rows: dict):
        self._rows = rows
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False  # never swallow an exception

    def close(self):
        self.closed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def execute(self, stmt):
        if isinstance(stmt, sa.sql.dml.Insert):
            return self._execute_insert(stmt)
        return self._execute_select(stmt)

    def _bound_values(self, stmt):
        return dict(stmt.compile(dialect=postgresql.dialect()).params)

    def _execute_insert(self, stmt):
        values = self._bound_values(stmt)
        game_id = values["game_id"]
        sql_text = str(stmt.compile(dialect=postgresql.dialect()))
        already_stored = game_id in self._rows
        if "DO NOTHING" in sql_text:
            if not already_stored:
                self._rows[game_id] = dict(values)
        else:  # DO UPDATE -- the upsert helper's own conflict policy
            if already_stored:
                self._rows[game_id]["payload"] = values["payload"]
                self._rows[game_id]["payload_version"] = values["payload_version"]
            else:
                self._rows[game_id] = dict(values)
        return _FakeResult(None)

    def _execute_select(self, stmt):
        game_id = next(iter(self._bound_values(stmt).values()))
        row = self._rows.get(game_id)
        return _FakeResult(dict(row) if row is not None else None)


def _repository_and_rows():
    """A fresh SqlAlchemyGameSessionRepository over a fresh, empty
    in-memory row store -- every _FakeSession the factory produces
    shares the same `rows` dict, the way real sessions against one
    real database would share its stored rows."""
    rows: dict = {}

    def session_factory():
        return _FakeSession(rows)

    return SqlAlchemyGameSessionRepository(session_factory), rows


def test_get_returns_none_for_a_missing_row():
    repository, _rows = _repository_and_rows()
    assert repository.get("does-not-exist") is None


def test_get_reconstructs_a_live_playable_game_session_from_a_stored_row():
    repository, _rows = _repository_and_rows()
    original = GameService().create_game()
    _scripted_throw(original, 3, (1, 2, 3))
    repository.put(original)

    restored = repository.get(original.game_id)

    assert restored is not None
    assert restored.game_id == original.game_id
    assert restored.current_snapshot() == original.current_snapshot()
    assert restored.lane.condition == original.lane.condition

    # And it's genuinely playable, not a read-only shell.
    _scripted_throw(restored, 4, (4, 5, 6, 7))
    assert restored.current_snapshot().frames[0].rolls == (3, 4)


def test_put_persists_the_current_record_via_the_upsert_helper():
    repository, rows = _repository_and_rows()
    session = GameService().create_game()

    repository.put(session)
    assert session.game_id in rows

    _scripted_throw(session, 5, (1, 2, 3, 4, 5))
    repository.put(session)  # write-back after a mutation, same as GameService.throw_in_game

    restored = repository.get(session.game_id)
    assert restored.current_snapshot() == session.current_snapshot()


def test_get_or_put_uses_insert_if_absent_on_the_missing_row_path():
    repository, rows = _repository_and_rows()

    factory_calls = []

    def factory():
        factory_calls.append(1)
        return GameService().create_game(game_id="brand-new-id")

    created = repository.get_or_put("brand-new-id", factory)

    assert created.game_id == "brand-new-id"
    assert len(factory_calls) == 1
    assert "brand-new-id" in rows
    # Read back through get(), independently, to prove it was genuinely
    # stored -- not just returned by get_or_put without persisting.
    assert repository.get("brand-new-id").current_snapshot() == created.current_snapshot()


def test_get_or_put_for_an_existing_id_never_calls_the_factory():
    repository, _rows = _repository_and_rows()
    existing = GameService().create_game()
    repository.put(existing)

    def factory_must_not_run():
        pytest.fail("factory must not run for an existing id")

    looked_up = repository.get_or_put(existing.game_id, factory_must_not_run)

    assert looked_up.current_snapshot() == existing.current_snapshot()


def test_get_or_put_does_not_overwrite_an_existing_row_with_the_factory_result():
    """The exact race the active task calls out: get_or_put must not
    collapse into a select-then-put that could clobber whatever is
    already stored for game_id."""
    repository, rows = _repository_and_rows()
    existing = GameService().create_game()
    _scripted_throw(existing, 7, (1, 2, 3, 4, 5, 6, 7))
    repository.put(existing)
    stored_payload_before = dict(rows[existing.game_id]["payload"])

    def factory_would_be_a_fresh_game():
        pytest.fail("factory must not run for an existing id")

    repository.get_or_put(existing.game_id, factory_would_be_a_fresh_game)

    assert rows[existing.game_id]["payload"] == stored_payload_before


def _capturing_session_factory(rows):
    """A session_factory that records every _FakeSession it produces, so
    a test can inspect the exact session one repository call used --
    there's no other way to reach it, since the repository only ever
    holds it locally inside one `with` block."""
    sessions_seen = []

    def session_factory():
        session = _FakeSession(rows)
        sessions_seen.append(session)
        return session

    return session_factory, sessions_seen


def test_a_failure_rolls_back_the_session_and_leaves_no_open_session():
    rows: dict = {}
    session_factory, sessions_seen = _capturing_session_factory(rows)
    repository = SqlAlchemyGameSessionRepository(session_factory)

    def factory_that_raises():
        raise RuntimeError("factory blew up mid-transaction")

    with pytest.raises(RuntimeError, match="factory blew up mid-transaction"):
        repository.get_or_put("will-fail", factory_that_raises)

    assert len(sessions_seen) == 1
    session = sessions_seen[0]
    assert session.rolled_back is True
    assert session.closed is True
    assert session.committed is False
    assert "will-fail" not in rows  # nothing was persisted


def test_a_get_failure_on_a_corrupted_row_also_rolls_back():
    """get() has nothing to commit, but a corrupted stored row still
    needs the same explicit rollback-before-raise discipline the
    mutating methods have -- see this module's own docstring."""
    rows: dict = {
        "corrupted-id": {
            "game_id": "corrupted-id",
            # game_id agrees with the row (passes record_from_row's own
            # check), but the payload itself is missing every other
            # required key -- record_from_payload must be what rejects it.
            "payload": {"game_id": "corrupted-id"},
            "payload_version": 1,
        }
    }
    session_factory, sessions_seen = _capturing_session_factory(rows)
    repository = SqlAlchemyGameSessionRepository(session_factory)

    with pytest.raises(GameSessionPayloadError):
        repository.get("corrupted-id")

    assert len(sessions_seen) == 1
    assert sessions_seen[0].rolled_back is True
    assert sessions_seen[0].closed is True


def test_importing_the_adapter_and_app_main_does_not_create_an_engine():
    """Same process-isolation pattern test_db_schema.py/test_db_session.py
    already use for this exact claim -- sabotages sqlalchemy.create_engine
    before either module is imported, then imports app.main and the new
    adapter module and confirms neither import triggered it."""
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    probe = (
        "import sqlalchemy\n"
        "calls = []\n"
        "def _sabotaged_create_engine(*a, **k):\n"
        "    calls.append((a, k))\n"
        "    raise AssertionError('create_engine was called during import')\n"
        "sqlalchemy.create_engine = _sabotaged_create_engine\n"
        "import app.main\n"
        "import app.db.sql_repository\n"
        "assert calls == [], f'create_engine was called during import: {calls}'\n"
        "print('PROBE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PROBE_OK" in result.stdout


def test_game_service_and_default_game_service_still_use_in_memory_repository():
    assert isinstance(GameService()._repository, InMemoryGameSessionRepository)
    from app.games.service import default_game_service

    assert isinstance(default_game_service._repository, InMemoryGameSessionRepository)
