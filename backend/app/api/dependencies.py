"""FastAPI dependency providers for the game-scoped API routes.

`get_game_service` is the one seam every route -- game-scoped
(`app.api.routes.games`) and the deprecated legacy one
(`app.api.routes.throws`) -- gets its `GameService` through, instead of
each route handler reaching for `app.games.service.default_game_service`
directly inside its own body. Its default returns that same
`default_game_service`: this milestone changes *how* a route obtains a
service, not which one it gets by default.

## Why this exists now

`app.db.sql_repository.SqlAlchemyGameSessionRepository` exists but is
deliberately unwired. Before any runtime, database-backed service could
be introduced safely, routes needed a seam a caller (a future
settings-driven wiring, or a test) can actually substitute through,
instead of every route function hardcoding one module-level global.
This module only adds that seam: default runtime behavior, `GameService()`'s
own default, and `default_game_service` itself are all unchanged, and
nothing here constructs an `Engine`, opens a connection, or even imports
`app.db`/`app.db.sql_repository` at all.

## Overriding in tests

FastAPI's own `app.dependency_overrides[get_game_service] = lambda: my_service`
is the intended override mechanism: set it on the real `app` instance
(`app.main.app`), and every route depending on `get_game_service`
receives `my_service` for as long as the override is set, with no
route-module global ever mutated -- see
`tests/test_api_game_service_dependency.py` for exactly this pattern,
including cleanup so an override never leaks into another test.
"""

from __future__ import annotations

from app.games.service import GameService, default_game_service


def get_game_service() -> GameService:
    """The one FastAPI dependency every game-scoped and legacy route
    resolves its `GameService` through. Returns the real
    `default_game_service` unless overridden via
    `app.dependency_overrides` -- see this module's own docstring."""
    return default_game_service
