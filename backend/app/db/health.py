"""One lightweight SQL connectivity probe, for `GET /health` in `"sql"`
storage mode -- see that route's own docstring in `app.main` for exactly
when this runs and what it reports.

Deliberately takes an already-built `sessionmaker` (from
`app.db.session.build_session_factory`), not a URL or `Settings` object,
so a test can hand it a fake or genuinely-unroutable factory directly
without needing a real database or monkeypatching `settings` -- the same
"pass the object, not the config that builds it" seam
`app.db.sql_repository.SqlAlchemyGameSessionRepository` already uses.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


def sql_database_is_reachable(session_factory: sessionmaker[Session]) -> bool:
    """True if a `Session` opened from `session_factory` can execute a
    trivial `SELECT 1`; false for any SQLAlchemy-level connection failure
    (refused connection, DNS failure, authentication failure, timeout).

    Never raises `SQLAlchemyError`: an unreachable database is the
    expected, handled outcome this function exists to report to
    `/health`, not an exceptional one a caller needs to guard against
    separately. Any other exception (a programming error, not a
    connectivity failure) still propagates unchanged.
    """
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
