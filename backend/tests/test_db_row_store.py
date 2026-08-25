"""`app.db.row_store`: the row-value/SQL-statement boundary between a
`GameSessionRecord` and the `game_sessions` table. Offline only, like
`test_db_schema.py` -- no test here opens a live database connection.
Statement helpers are checked by compiling against the PostgreSQL
dialect, not by executing anything.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.row_store import (
    PAYLOAD_VERSION,
    GameSessionRowError,
    record_from_row,
    record_to_row_values,
    select_game_session_stmt,
    upsert_game_session_stmt,
)
from app.games.record_payload import GameSessionPayloadError, record_to_payload
from app.games.service import GameService, GameSession, InMemoryGameSessionRepository
from app.physics.ball import BALL_CATALOG
from app.physics.pinfall import PinfallResult
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw

BALL = BALL_CATALOG["house_ball"]
THROW = Throw()


def _scripted_throw(session, pins_knocked, fallen_pin_ids):
    """Same pattern as test_game_session_lifecycle.py's identical helper."""

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


def _worn_record():
    session = GameService().create_game()
    _scripted_throw(session, 3, (1, 2, 3))
    _scripted_throw(session, 4, (4, 5, 6, 7))
    return session, session.to_record()


# --- record_to_row_values / record_from_row -------------------------------


def test_record_to_row_values_has_exactly_the_expected_keys():
    _session, record = _worn_record()
    row_values = record_to_row_values(record)

    assert set(row_values) == {"game_id", "payload", "payload_version"}
    # created_at/updated_at are the table's own database-side defaults --
    # application code never stamps a value into either.


def test_record_to_row_values_uses_the_current_payload_version_constant():
    _session, record = _worn_record()
    row_values = record_to_row_values(record)

    assert row_values["payload_version"] == PAYLOAD_VERSION
    assert PAYLOAD_VERSION == 1


def test_record_to_row_values_payload_matches_record_to_payload_directly():
    _session, record = _worn_record()
    row_values = record_to_row_values(record)

    assert row_values["payload"] == record_to_payload(record)
    assert row_values["game_id"] == record.game_id


def test_row_round_trips_a_fresh_game():
    session = GameService().create_game()
    record = session.to_record()

    row = record_to_row_values(record)
    rebuilt = record_from_row(row)

    assert rebuilt == record
    restored = GameSession.from_record(rebuilt)
    assert restored.current_snapshot() == session.current_snapshot()


def test_row_round_trips_a_worn_partial_rack_game():
    session, record = _worn_record()

    row = record_to_row_values(record)
    rebuilt = record_from_row(row)

    assert rebuilt == record
    restored = GameSession.from_record(rebuilt)
    assert restored.current_snapshot() == session.current_snapshot()
    assert restored.lane.condition == session.lane.condition

    # And the restored session keeps playing, not starting fresh: frame 1
    # was already complete (3, 4) at capture time, so this roll is frame
    # 2's first ball, not a fresh frame 3.
    _scripted_throw(restored, 1, (10,))
    assert restored.current_snapshot().frames[0].rolls == (3, 4)
    assert restored.current_snapshot().frames[1].rolls == (1,)


def test_record_from_row_rejects_a_missing_payload_version():
    _session, record = _worn_record()
    row = record_to_row_values(record)
    del row["payload_version"]

    with pytest.raises(GameSessionRowError, match="payload_version"):
        record_from_row(row)


def test_record_from_row_rejects_an_unsupported_payload_version():
    _session, record = _worn_record()
    row = record_to_row_values(record)
    row["payload_version"] = PAYLOAD_VERSION + 1

    with pytest.raises(GameSessionRowError, match="unsupported"):
        record_from_row(row)


def test_record_from_row_rejects_a_missing_payload():
    _session, record = _worn_record()
    row = record_to_row_values(record)
    del row["payload"]

    with pytest.raises(GameSessionRowError, match="payload"):
        record_from_row(row)


def test_record_from_row_rejects_a_non_mapping_payload():
    _session, record = _worn_record()
    row = record_to_row_values(record)
    row["payload"] = "not-a-mapping"

    with pytest.raises(GameSessionRowError, match="mapping"):
        record_from_row(row)


def test_record_from_row_rejects_a_missing_game_id():
    _session, record = _worn_record()
    row = record_to_row_values(record)
    del row["game_id"]

    with pytest.raises(GameSessionRowError, match="game_id"):
        record_from_row(row)


def test_record_from_row_rejects_a_row_payload_game_id_mismatch():
    _session, record = _worn_record()
    row = record_to_row_values(record)
    row["game_id"] = "a-totally-different-id"

    with pytest.raises(GameSessionRowError, match="does not match"):
        record_from_row(row)


def test_record_from_row_delegates_malformed_payload_structure_to_record_from_payload():
    """A row-level check (payload_version, presence, game_id agreement)
    is this module's own job -- a *structural* problem inside an
    otherwise well-formed payload is not, and must still surface as
    GameSessionPayloadError, not a second, looser GameSessionRowError."""
    _session, record = _worn_record()
    row = record_to_row_values(record)
    row["payload"] = dict(row["payload"])
    del row["payload"]["rolls"]

    with pytest.raises(GameSessionPayloadError):
        record_from_row(row)


# --- SQL statement helpers --------------------------------------------


def test_select_game_session_stmt_compiles_with_postgresql_dialect():
    stmt = select_game_session_stmt("some-id")
    compiled = stmt.compile(dialect=postgresql.dialect())

    assert "game_sessions" in str(compiled)
    assert "some-id" in compiled.params.values()


def test_select_game_session_stmt_filters_on_game_id():
    stmt = select_game_session_stmt("some-id")
    sql_text = str(stmt.compile(dialect=postgresql.dialect()))

    assert "WHERE game_sessions.game_id" in sql_text


def test_upsert_game_session_stmt_compiles_with_postgresql_dialect():
    _session, record = _worn_record()
    stmt = upsert_game_session_stmt(record)
    compiled = stmt.compile(dialect=postgresql.dialect())

    assert record.game_id in compiled.params.values()


def test_upsert_game_session_stmt_is_an_on_conflict_do_update_for_game_id():
    _session, record = _worn_record()
    stmt = upsert_game_session_stmt(record)
    sql_text = str(stmt.compile(dialect=postgresql.dialect()))

    assert "INSERT INTO game_sessions" in sql_text
    assert "ON CONFLICT (game_id) DO UPDATE SET" in sql_text


def test_upsert_game_session_stmt_updates_payload_and_version_but_leaves_created_at_alone():
    _session, record = _worn_record()
    stmt = upsert_game_session_stmt(record)
    sql_text = str(stmt.compile(dialect=postgresql.dialect()))

    set_clause = sql_text.split("DO UPDATE SET", 1)[1]
    assert "payload = excluded.payload" in set_clause
    assert "payload_version = excluded.payload_version" in set_clause
    assert "updated_at" in set_clause
    # created_at is deliberately never part of the UPDATE -- an existing
    # row's original creation time must survive a later upsert untouched.
    assert "created_at" not in set_clause


def test_upsert_game_session_stmt_does_not_open_a_connection():
    """Compiling (and even constructing) a statement must never require
    a database -- this just re-confirms compile() above ran with no
    Engine/connection anywhere in reach, using sa.Insert's own type as a
    sanity check that this is genuinely a statement object, not a result."""
    _session, record = _worn_record()
    stmt = upsert_game_session_stmt(record)
    assert isinstance(stmt, sa.sql.dml.Insert)


# --- scope boundary: runtime storage is unaffected ---------------------


def test_game_service_and_default_game_service_still_use_in_memory_repository():
    assert isinstance(GameService()._repository, InMemoryGameSessionRepository)
    from app.games.service import default_game_service

    assert isinstance(default_game_service._repository, InMemoryGameSessionRepository)
