"""FastAPI dependency providers for the game-scoped API routes.

`get_game_service` is the one seam every route -- game-scoped
(`app.api.routes.games`) and the deprecated legacy one
(`app.api.routes.throws`) -- gets its `GameService` through, instead of
each route handler reaching for `app.games.service.default_game_service`
directly inside its own body.

## Storage mode

`get_game_service` (via `build_configured_game_service`, below) reads
`settings.game_storage_mode` on every call and builds the corresponding
`GameService`:

- `"memory"` (the default) returns the real `default_game_service`
  unchanged -- the exact same object, same behavior, as before this
  setting existed. Nothing about import, app startup, or a
  `"memory"`-mode request creates a SQLAlchemy `Engine`, opens a
  connection, begins a session, runs a migration, or queries a database.
- `"sql"` builds a fresh `GameService` backed by
  `SqlAlchemyGameSessionRepository`, using `app.db.session.build_engine`/
  `build_session_factory` against `settings.database_url`. This is the
  first place in the whole codebase those functions and that repository
  are actually wired together and used by a real request -- everything
  built before this milestone only proved each piece works in isolation.
- any other value raises `ValueError` -- a controlled configuration
  error, not a silent fallback to `"memory"`. `Settings.game_storage_mode`
  is typed `Literal["memory", "sql"]`, so pydantic already rejects an
  unrecognized `GAME_STORAGE_MODE` environment value at app-startup
  settings-construction time; this runtime check exists for whatever
  gets past that (most directly, a test that mutates an
  already-constructed `settings` object directly), so an unsupported
  value fails the same clear way regardless of how it got there.

Not a finished production integration: `"sql"` mode builds a fresh
`Engine`/session factory on every call rather than reusing one across
requests, runs no migrations, has no connection retry or health check,
and if the configured database isn't actually reachable, a `"sql"`-mode
request fails at connection time, not at configuration time. This
milestone is the wiring and its tests, not production readiness -- see
the README's own known-limitations section for what's still true
regardless of this setting.

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

from app.core.config import settings
from app.db.session import build_engine, build_session_factory
from app.db.sql_repository import SqlAlchemyGameSessionRepository
from app.games.service import GameService, default_game_service


def build_configured_game_service() -> GameService:
    """Builds the `GameService` `settings.game_storage_mode` currently
    selects -- see this module's own docstring for exactly what each
    mode does. A pure function of the current settings value, callable
    directly (by a test, or by `get_game_service` below) without going
    through FastAPI's dependency injection machinery.
    """
    mode = settings.game_storage_mode
    if mode == "memory":
        return default_game_service
    if mode == "sql":
        engine = build_engine()
        session_factory = build_session_factory(engine)
        return GameService(repository=SqlAlchemyGameSessionRepository(session_factory))
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
