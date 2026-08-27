"""`GET /health`: `{"status": "ok"}` unconditionally in the default
`"memory"` storage mode (byte-for-byte unchanged from before this
milestone), and a real `SELECT 1` connectivity check in `"sql"` mode --
see `app.main.health`'s own docstring for the exact shape either way.

`settings` (from `app.core.config`) is a module-level singleton shared by
the whole test suite -- every mutation here goes through
`monkeypatch.setattr`, the same discipline
`test_api_game_service_storage_mode.py` already establishes, so nothing
leaks `game_storage_mode = "sql"` into another test file.
"""

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app

client = TestClient(app)

# Same deliberately-unroutable pattern used throughout this
# persistence-infrastructure arc (test_db_session.py,
# test_api_game_service_storage_mode.py, test_db_health.py): port 1 on
# localhost refuses connections immediately rather than hanging.
UNROUTABLE_URL = "postgresql+psycopg2://probe_user:probe_pass@localhost:1/probe_db"


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_default_storage_mode_is_memory():
    assert settings.game_storage_mode == "memory"


def test_memory_mode_never_attempts_a_database_connectivity_check(monkeypatch):
    """The scope boundary this milestone must not cross: default
    `"memory"` mode must not build an Engine or call the new probe at
    all, not just happen to report `{"status": "ok"}` anyway."""

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("sql_database_is_reachable was called in memory mode")

    monkeypatch.setattr(main_module, "sql_database_is_reachable", _fail_if_called)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sql_mode_reports_ok_for_a_reachable_database(monkeypatch):
    monkeypatch.setattr(settings, "game_storage_mode", "sql")
    monkeypatch.setattr(settings, "database_url", "sqlite:///:memory:")
    # SQLAlchemy accepts a bare "sqlite:///:memory:" URL directly, so the
    # real build_engine()/build_session_factory() code path in the route
    # runs unmodified -- this is a genuine SQLite connection and a real
    # executed SELECT 1, not a mock standing in for the whole check.

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_sql_mode_reports_unreachable_for_an_unroutable_database(monkeypatch):
    """The specific regression this milestone exists to fix: a
    misconfigured/unreachable database must produce a clear, handled
    failure response, not an unhandled exception (a 500) and not a
    false "ok"."""
    monkeypatch.setattr(settings, "game_storage_mode", "sql")
    monkeypatch.setattr(settings, "database_url", UNROUTABLE_URL)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}
