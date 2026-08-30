"""FastAPI dependency providers for the game-scoped API routes.

`get_game_service` is the one seam every route -- game-scoped
(`app.api.routes.games`) and the deprecated legacy one
(`app.api.routes.throws`) -- gets its `GameService` through, instead of
each route handler reaching for `app.games.service.default_game_service`
directly inside its own body.

## Storage mode

`get_game_service` (via `build_configured_game_service`, below) reads
`settings.game_storage_mode` on every call and returns the corresponding
process-scoped `GameService`:

- `"memory"` (the default) returns the real `default_game_service`
  unchanged -- the exact same object, same behavior, as before this
  setting existed. Nothing about import, app startup, or a
  `"memory"`-mode request creates a SQLAlchemy `Engine`, opens a
  connection, begins a session, runs a migration, or queries a database.
- `"sql"` lazily builds one `GameService` backed by
  `SqlAlchemyGameSessionRepository`, using `app.db.session.build_engine`/
  `build_session_factory` against `settings.database_url`, then reuses it
  for every request in this process. The cache is keyed by database URL so
  a test or embedding process that deliberately changes configuration gets
  a separate service rather than one bound to the old engine.
- any other value raises `ValueError` -- a controlled configuration
  error, not a silent fallback to `"memory"`. `Settings.game_storage_mode`
  is typed `Literal["memory", "sql"]`, so pydantic already rejects an
  unrecognized `GAME_STORAGE_MODE` environment value at app-startup
  settings-construction time; this runtime check exists for whatever
  gets past that (most directly, a test that mutates an
  already-constructed `settings` object directly), so an unsupported
  value fails the same clear way regardless of how it got there.

Not a finished production integration: `"sql"` mode runs no migrations
and has no connection retry; if the configured database isn't actually
reachable, a `"sql"`-mode request fails at connection time, not at
configuration time. Service reuse and mutation locking are process-local,
so a future multi-worker deployment still needs database-level concurrency
control. See the README's own known-limitations section for the remaining
scope.

## Overriding in tests

FastAPI's own `app.dependency_overrides[get_game_service] = lambda: my_service`
is the intended override mechanism, unaffected by `game_storage_mode`:
set it on the real `app` instance (`app.main.app`), and every route
depending on `get_game_service` receives `my_service` for as long as the
override is set, regardless of what storage mode is configured -- see
`tests/test_api_game_service_dependency.py` for exactly this pattern,
including cleanup so an override never leaks into another test.
"""

from __future__ import annotations

import threading

from app.core.config import settings
from app.db.session import build_engine, build_session_factory
from app.db.sql_repository import SqlAlchemyGameSessionRepository
from app.games.service import GameService, default_game_service

_sql_services: dict[str, GameService] = {}
_sql_services_lock = threading.Lock()


def _get_sql_game_service(database_url: str) -> GameService:
    """Returns the one SQL-backed service for `database_url` in this process.

    The lock covers lookup and construction together. `create_engine` is
    lazy, so this short critical section cannot block on the database and
    prevents two first requests from escaping with different service/lock
    registries.
    """
    with _sql_services_lock:
        service = _sql_services.get(database_url)
        if service is None:
            engine = build_engine(database_url)
            session_factory = build_session_factory(engine)
            service = GameService(repository=SqlAlchemyGameSessionRepository(session_factory))
            _sql_services[database_url] = service
        return service


def build_configured_game_service() -> GameService:
    """Returns the process-scoped service selected by the current settings.

    Callable directly (by a test, or by `get_game_service` below) without
    going through FastAPI's dependency injection machinery. SQL service
    construction stays lazy: this function builds no engine at import time,
    and `create_engine` opens no connection when the service is first built.
    """
    mode = settings.game_storage_mode
    if mode == "memory":
        return default_game_service
    if mode == "sql":
        return _get_sql_game_service(settings.database_url)
    raise ValueError(f"Unsupported game_storage_mode {mode!r}")


def get_game_service() -> GameService:
    """The one FastAPI dependency every game-scoped and legacy route
    resolves its `GameService` through. Delegates to
    `build_configured_game_service` -- see this module's own docstring
    for storage-mode behavior, and for the `app.dependency_overrides`
    mechanism tests use instead of relying on this function's own return
    value.
    """
    return build_configured_game_service()
