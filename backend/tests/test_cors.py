"""Backend CORS: off by default, opt-in via `BACKEND_CORS_ORIGINS`.

Two things are proven separately here, deliberately: the real app
(`app.main.app`) sends no CORS allow headers to anyone out of the box, and
`configure_cors` (the function that would install `CORSMiddleware`) does
the right thing for a configured allowlist. The second half runs against a
fresh `FastAPI()` instance rather than mutating the real, already-built
`app` -- middleware is only ever added once at construction time in this
project, so there is no clean way to add-then-remove it from the real
`app` between tests without either leaking state across tests or reaching
for private Starlette internals.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from app.core.config import Settings
from app.main import app, configure_cors

client = TestClient(app)


def _cors_configured_app() -> FastAPI:
    """A minimal FastAPI app with the same /health route as the real one,
    CORS configured for exactly one origin -- isolated from app.main.app
    so these tests can't affect (or be affected by) each other or by the
    real app's own middleware stack."""
    test_app = FastAPI()

    @test_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    configure_cors(test_app, ["https://frontend.example"])
    return test_app


def test_default_app_sends_no_cors_header_for_a_cross_origin_request():
    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_default_app_has_no_cors_middleware_installed():
    """Stronger than checking one response's headers: proves the actual
    mechanism is absent, not just incidentally silent for this one
    request shape."""
    assert not any(m.cls is CORSMiddleware for m in app.user_middleware)


def test_configure_cors_with_an_empty_allowlist_installs_no_middleware():
    """The exact contract configure_cors documents: an empty list -- the
    Settings default -- is a no-op, not "allow nothing" via a
    zero-origin CORSMiddleware."""
    test_app = FastAPI()

    configure_cors(test_app, [])

    assert not any(m.cls is CORSMiddleware for m in test_app.user_middleware)


def test_configured_allowlist_returns_the_header_for_an_allowed_origin():
    test_client = TestClient(_cors_configured_app())

    response = test_client.get("/health", headers={"Origin": "https://frontend.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example"


def test_configured_allowlist_omits_the_header_for_an_unlisted_origin():
    test_client = TestClient(_cors_configured_app())

    response = test_client.get("/health", headers={"Origin": "https://not-allowed.example"})

    # The request itself still succeeds server-side (CORS is enforced by
    # the browser reading this header, not the server refusing the
    # request) -- what matters is that no allow header names this origin.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_configured_allowlist_handles_a_preflight_request():
    test_client = TestClient(_cors_configured_app())

    response = test_client.options(
        "/health",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example"


def test_configured_allowlist_never_allows_credentials():
    """No auth/session layer exists yet -- credentialed CORS would be a
    real security posture change with no feature behind it, so this is
    pinned as a regression rather than left to configure_cors's own
    hardcoded allow_credentials=False silently drifting."""
    test_client = TestClient(_cors_configured_app())

    response = test_client.get("/health", headers={"Origin": "https://frontend.example"})

    assert response.headers.get("access-control-allow-credentials") != "true"


def test_backend_cors_origins_defaults_to_an_empty_list(monkeypatch):
    monkeypatch.delenv("BACKEND_CORS_ORIGINS", raising=False)

    assert Settings().backend_cors_origins == []


def test_backend_cors_origins_parses_a_json_array_from_the_environment(monkeypatch):
    monkeypatch.setenv(
        "BACKEND_CORS_ORIGINS", '["https://frontend.example", "https://other.example"]'
    )

    assert Settings().backend_cors_origins == [
        "https://frontend.example",
        "https://other.example",
    ]


def test_backend_cors_origins_strips_blank_and_empty_entries(monkeypatch):
    monkeypatch.setenv(
        "BACKEND_CORS_ORIGINS", '["", "https://frontend.example", "   ", "https://other.example"]'
    )

    assert Settings().backend_cors_origins == [
        "https://frontend.example",
        "https://other.example",
    ]


def test_backend_cors_origins_empty_array_stays_empty(monkeypatch):
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "[]")

    assert Settings().backend_cors_origins == []
