"""The first concrete, SQL-backed `GameSessionRepository` implementation
-- opt-in and unwired. Nothing in `GameService`, `default_game_service`,
any API route, app startup, or the test suite's default configuration
constructs or uses this class; it exists so the interface
`app.games.service.GameSessionRepository` describes has a second,
real-database implementation to prove out, alongside
`InMemoryGameSessionRepository`. A caller (a future runtime wiring, or a
test that constructs it directly) supplies a session factory; this
module never builds one, opens a connection, or picks a database on its
own.

## Layering

`SqlAlchemyGameSessionRepository` is the composition point for
everything the persistence-infrastructure milestones before this one
built: `app.db.session.build_session_factory` supplies the session
factory this class is constructed with; `app.db.row_store`'s three
statement helpers (`select_game_session_stmt`, `upsert_game_session_stmt`,
`insert_if_absent_game_session_stmt`) are the only SQL this class ever
runs; `record_from_row`/`GameSession.to_record`/`GameSession.from_record`
are the only conversions between a stored row and a live `GameSession`.
This class adds no new SQL, no new payload shape, and no new validation
of its own -- it only sequences existing pieces against a real session.

## Session lifecycle

Every method opens its own session (via the injected `session_factory`,
typically a real `sqlalchemy.orm.sessionmaker` from
`app.db.session.build_session_factory`) inside a `with` block, so a
session's lifetime never outlives one repository call. A method that
mutates state (`put`, and `get_or_put`'s create branch) explicitly
commits on success and explicitly rolls back before re-raising on any
failure -- never left implicit, and never left for the session's own
`__exit__`/`close()` to paper over silently. `get`'s read-only path
rolls back the same way on a failure decoding an already-stored row
(e.g. `GameSessionRowError`/`GameSessionPayloadError` for a corrupted
row), even though it has nothing of its own to commit -- the same
explicit, local discipline applies uniformly, not only to the methods
that write.

## `get_or_put`'s atomicity

See `GameSessionRepository.get_or_put`'s own docstring for why a plain
"check, then conditionally store" is exactly the race a repository
boundary must not introduce. This implementation reads first (so
`factory()` -- which can be non-trivial work -- only ever runs for a
genuinely missing `game_id`, the same restraint
`InMemoryGameSessionRepository.get_or_put` already exercises), but the
actual atomicity guarantee comes from `insert_if_absent_game_session_stmt`'s
`ON CONFLICT (game_id) DO NOTHING`, not from the initial read: two
concurrent callers can both see "missing," both attempt the
insert-if-absent, and the database itself ensures only one insert
actually lands. The read-back after that insert attempt is what makes
this correct regardless of which caller "won" -- it returns whatever row
is now actually stored, never blindly returns the local candidate
`factory()` just built.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.db.row_store import (
    insert_if_absent_game_session_stmt,
    record_from_row,
    select_game_session_stmt,
    upsert_game_session_stmt,
)
from app.games.service import GameSession, GameSessionRepository


class SqlAlchemyGameSessionRepository(GameSessionRepository):
    """A `GameSessionRepository` backed by a real SQLAlchemy session
    factory -- see this module's own docstring for the full design.
    Constructing an instance opens no connection and creates no engine;
    `session_factory` (typically `app.db.session.build_session_factory(engine)`'s
    return value) is only ever called from inside `get`/`put`/`get_or_put`,
    once per call.
    """

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def get(self, game_id: str) -> GameSession | None:
        with self._session_factory() as db_session:
            try:
                row = db_session.execute(select_game_session_stmt(game_id)).mappings().one_or_none()
                if row is None:
                    return None
                # dict(row): RowMapping's declared key type isn't exactly
                # `str` under strict mypy, even though every real key here
                # is one -- a plain dict satisfies record_from_row's
                # Mapping[str, Any] parameter without changing what it holds.
                record = record_from_row(dict(row))
                return GameSession.from_record(record)
            except Exception:
                db_session.rollback()
                raise

    def put(self, session: GameSession) -> None:
        with self._session_factory() as db_session:
            try:
                db_session.execute(upsert_game_session_stmt(session.to_record()))
                db_session.commit()
            except Exception:
                db_session.rollback()
                raise

    def get_or_put(self, game_id: str, factory: Callable[[], GameSession]) -> GameSession:
        with self._session_factory() as db_session:
            try:
                existing_row = (
                    db_session.execute(select_game_session_stmt(game_id)).mappings().one_or_none()
                )
                if existing_row is not None:
                    return GameSession.from_record(record_from_row(dict(existing_row)))

                candidate = factory()
                db_session.execute(insert_if_absent_game_session_stmt(candidate.to_record()))
                db_session.commit()

                # Authoritative read-back, not the local candidate -- see
                # "get_or_put's atomicity" in this module's own docstring
                # for why a concurrent winner's row must be what this
                # returns, not necessarily `candidate`.
                stored_row = db_session.execute(select_game_session_stmt(game_id)).mappings().one()
                return GameSession.from_record(record_from_row(dict(stored_row)))
            except Exception:
                db_session.rollback()
                raise
