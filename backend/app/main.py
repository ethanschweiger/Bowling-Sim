"""FastAPI entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.balls import router as balls_router
from app.api.routes.games import router as games_router
from app.api.routes.oil_patterns import router as oil_patterns_router
from app.api.routes.throws import router as throws_router
from app.core.config import settings


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
def health() -> dict[str, str]:
    return {"status": "ok"}
