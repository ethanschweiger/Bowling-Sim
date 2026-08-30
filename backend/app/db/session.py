"""Opt-in SQLAlchemy `Engine`/`Session` factory for the `game_sessions`
table, used by the SQL-backed `GameSessionRepository`.

Deliberately lazy and inert: importing this module (or `app.main`, or
`app.db`) never calls `create_engine`, opens a connection, begins a
session, runs a migration, or queries anything. Every function here only
*builds and returns* an object a caller would use later -- the same
"statement, not an executed query" discipline `app.db.row_store` already
applies to SQL statements, extended one layer further out to the
engine/session objects those statements would eventually run through.
SQLAlchemy's own `create_engine` is itself lazy this same way: it never
opens a network connection until something actually executes against the
`Engine` it returns (a `Session`, a raw `.connect()` call, and so on) --
calling `build_engine` alone touches no network and no database.

## Runtime wiring

`app.api.dependencies` calls `build_engine()` and
`build_session_factory(engine)` when SQL mode is selected, then reuses that
service within the process. The functions remain lazy and do not migrate or
connect until a repository operation executes.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def build_engine(database_url: str | None = None) -> Engine:
    """Builds a SQLAlchemy `Engine` for `database_url`, or
    `settings.database_url` (the same env-driven connection string
    `alembic/env.py` already reads) if `database_url` is omitted. The
    URL is passed through to `create_engine` exactly as given -- never
    redacted, rewritten, or replaced with a hardcoded value -- so a
    caller who passes an explicit URL (a test, most likely) gets exactly
    the engine that URL describes, not a stand-in for it.
    """
    return create_engine(database_url or settings.database_url)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A `sessionmaker` bound to `engine`, for a caller to build `Session`
    instances from later. Constructing a `sessionmaker` never opens a
    connection itself -- only actually using a `Session` it produces
    (a query, a commit, an explicit `.connect()`) does."""
    return sessionmaker(bind=engine)
