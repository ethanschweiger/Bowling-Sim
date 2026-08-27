"""FastAPI entry point."""

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.balls import router as balls_router
from app.api.routes.games import router as games_router
from app.api.routes.oil_patterns import router as oil_patterns_router
from app.api.routes.throws import router as throws_router
from app.core.config import settings
from app.db.health import sql_database_is_reachable
from app.db.session import build_engine, build_session_factory


def configure_cors(app: FastAPI, allowed_origins: list[str]) -> None:
    """Installs `CORSMiddleware` only when there's a real allowlist to
    enforce. An empty list (the default) leaves the app exactly as it was
    before this setting existed -- no CORS headers on any response, so a
    same-origin setup (Vite's dev-server proxy, or any future same-origin
    deployment) is completely unaffected. `allow_credentials` stays off:
    there's no auth layer or cookie-based session today, so nothing needs
    credentialed cross-origin requests, and turning it on prematurely
    would be a real security posture change with no feature behind it.
    Kept as a standalone function (rather than inlined below) so a test
    can exercise both branches -- default-empty and configured -- against
    a fresh `FastAPI()` instance without needing to reload this module or
    mutate the real, already-constructed `app`.
    """
    if not allowed_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


app = FastAPI(title=settings.app_name)
configure_cors(app, settings.backend_cors_origins)

app.include_router(balls_router, prefix=settings.api_v1_prefix)
app.include_router(games_router, prefix=settings.api_v1_prefix)
app.include_router(oil_patterns_router, prefix=settings.api_v1_prefix)
app.include_router(throws_router, prefix=settings.api_v1_prefix)


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    """Default `"memory"` storage mode (unchanged from before this
    connectivity check existed): always `{"status": "ok"}`, HTTP 200 --
    no engine is ever constructed, no database is ever touched.

    `"sql"` storage mode: attempts one lightweight connectivity check
    (`SELECT 1`, via a fresh `Engine`/session built the same lazy way
    `app.api.dependencies.build_configured_game_service` already builds
    its own, not a shared/cached one) and reports the result --
    `{"status": "ok", "database": "ok"}` at HTTP 200 if it succeeds, or
    `{"status": "degraded", "database": "unreachable"}` at HTTP 503 if it
    doesn't. Never raises for a connectivity failure -- see
    `app.db.health.sql_database_is_reachable`'s own docstring for exactly
    what it catches.
    """
    if settings.game_storage_mode != "sql":
        return {"status": "ok"}

    engine = build_engine()
    session_factory = build_session_factory(engine)
    if sql_database_is_reachable(session_factory):
        return {"status": "ok", "database": "ok"}

    response.status_code = 503
    return {"status": "degraded", "database": "unreachable"}
