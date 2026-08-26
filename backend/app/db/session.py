"""Opt-in SQLAlchemy `Engine`/`Session` factory for the `game_sessions`
table -- plumbing a future persistent `GameSessionRepository` would build
on, not something anything in this codebase calls today.

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

## Why this exists, and why it isn't wired in yet

A future persistent `GameSessionRepository` will need a real `Engine` and
a way to open `Session`s against it. Rather than let that repository
invent its own ad hoc `create_engine()` call, this module is the one
place that choice lives -- import it, call `build_engine()` then
`build_session_factory(engine)`, and every future caller gets the same
configuration. Nothing calls either function yet: `GameService`,
`default_game_service`, every API route, app startup, Docker, and the
test suite all still run entirely on `InMemoryGameSessionRepository`,
with zero database connection anywhere in their own call graphs. Adding
this factory is not itself a claim that persistence works, or that games
survive a restart -- see `app.games.service`'s own module docstring, and
the README, for what's still true regardless.
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
