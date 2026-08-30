"""`app.api.dependencies.build_configured_game_service`/`get_game_service`:
the opt-in `settings.game_storage_mode` selection between
`InMemoryGameSessionRepository` (the default) and
`SqlAlchemyGameSessionRepository`.

Offline only, like every other test in this persistence-infrastructure
arc -- no test here opens a real database connection. `"sql"` mode is
exercised against a deliberately unroutable URL throughout, so a test
that accidentally tried to actually use the resulting service against a
database would fail fast and obviously, not hang or silently succeed.

`settings` (from `app.core.config`) is a module-level singleton shared
by the whole test suite -- every mutation here goes through
`monkeypatch.setattr`, never direct attribute assignment, so nothing
leaks into another test file. A leaked `game_storage_mode = "sql"` would
be a suite-wide regression: every other test that calls
`get_game_service()` would suddenly try to build a SQL-backed service
instead of using `default_game_service`.
"""

from concurrent.futures import ThreadPoolExecutor

import pydantic
import pytest

from app.api.dependencies import build_configured_game_service, get_game_service
from app.core.config import Settings, settings
from app.db.sql_repository import SqlAlchemyGameSessionRepository
from app.games.service import GameService, InMemoryGameSessionRepository, default_game_service

UNROUTABLE_URL = "postgresql+psycopg2://probe_user:probe_pass@localhost:1/probe_db"


def test_default_storage_mode_is_memory():
    assert settings.game_storage_mode == "memory"


def test_memory_mode_builds_the_real_default_game_service():
    assert build_configured_game_service() is default_game_service


def test_get_game_service_returns_default_game_service_in_default_memory_mode():
    assert get_game_service() is default_game_service


def test_sql_mode_builds_a_service_backed_by_the_sql_repository(monkeypatch):
    monkeypatch.setattr(settings, "game_storage_mode", "sql")
    monkeypatch.setattr(settings, "database_url", UNROUTABLE_URL)

    service = build_configured_game_service()

    assert isinstance(service, GameService)
    assert isinstance(service._repository, SqlAlchemyGameSessionRepository)
    # And genuinely not the real in-memory singleton -- a separately
    # configured process-scoped service, not default_game_service wearing a
    # different label.
    assert service is not default_game_service


def test_sql_mode_reuses_one_service_for_concurrent_requests(monkeypatch):
    """The API dependency must not give first requests separate lock registries.

    `create_engine` is lazy, so this remains an offline test even though it
    exercises the real SQL service construction path.
    """
    database_url = "postgresql+psycopg2://probe_user:probe_pass@localhost:1/reuse_probe"
    monkeypatch.setattr(settings, "game_storage_mode", "sql")
    monkeypatch.setattr(settings, "database_url", database_url)

    with ThreadPoolExecutor(max_workers=8) as executor:
        services = list(executor.map(lambda _index: get_game_service(), range(32)))

    assert len({id(service) for service in services}) == 1
    repository = services[0]._repository
    assert isinstance(repository, SqlAlchemyGameSessionRepository)
    engine = repository._session_factory.kw["bind"]
    assert engine.url.render_as_string(hide_password=False) == database_url


def test_sql_mode_uses_the_configured_database_url(monkeypatch):
    monkeypatch.setattr(settings, "game_storage_mode", "sql")
    monkeypatch.setattr(settings, "database_url", UNROUTABLE_URL)

    service = build_configured_game_service()

    engine = service._repository._session_factory.kw["bind"]
    assert engine.url.render_as_string(hide_password=False) == UNROUTABLE_URL


def test_sql_mode_does_not_open_a_connection(monkeypatch):
    """Building the service is pure object wiring -- it must succeed
    instantly against a URL that would fail immediately if anything
    actually tried to connect through it."""
    monkeypatch.setattr(settings, "game_storage_mode", "sql")
    monkeypatch.setattr(settings, "database_url", UNROUTABLE_URL)

    service = build_configured_game_service()

    assert service is not None  # constructing it never raised/hung


def test_invalid_storage_mode_raises_a_clear_configuration_error(monkeypatch):
    """Settings itself already rejects an out-of-Literal GAME_STORAGE_MODE
    env value at construction time (see
    test_settings_rejects_an_invalid_game_storage_mode_value_at_construction
    below) -- this covers whatever reaches get_game_service some other
    way, most directly a test (or a future caller) mutating an
    already-constructed settings object directly, same as this one does."""
    monkeypatch.setattr(settings, "game_storage_mode", "bogus")

    with pytest.raises(ValueError, match="bogus"):
        build_configured_game_service()


def test_settings_rejects_an_invalid_game_storage_mode_value_at_construction():
    """The Literal-typed field itself: pydantic-settings rejects an
    unrecognized GAME_STORAGE_MODE before the app ever starts, not just
    when get_game_service happens to be called."""
    with pytest.raises(pydantic.ValidationError):
        Settings(game_storage_mode="bogus")


def test_game_service_and_default_game_service_still_use_in_memory_repository():
    """The scope boundary every task in this persistence arc has
    carried: nothing about adding this opt-in wiring changes what
    GameService()/default_game_service use by default."""
    assert isinstance(GameService()._repository, InMemoryGameSessionRepository)
    assert isinstance(default_game_service._repository, InMemoryGameSessionRepository)


def test_importing_app_main_and_dependencies_does_not_create_an_engine():
    """Same process-isolation pattern used throughout this persistence
    arc's own test files (test_db_schema.py, test_db_session.py,
    test_db_sql_repository.py, test_api_game_service_dependency.py) for
    this exact claim -- sabotages sqlalchemy.create_engine before either
    module is imported, then imports app.main and app.api.dependencies
    (with the real, default "memory" settings -- nothing here overrides
    game_storage_mode) and confirms neither import triggered it.
    """
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    probe = (
        "import sqlalchemy\n"
        "calls = []\n"
        "def _sabotaged_create_engine(*a, **k):\n"
        "    calls.append((a, k))\n"
        "    raise AssertionError('create_engine was called during import')\n"
        "sqlalchemy.create_engine = _sabotaged_create_engine\n"
        "import app.main\n"
        "import app.api.dependencies\n"
        "assert calls == [], f'create_engine was called during import: {calls}'\n"
        "print('PROBE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PROBE_OK" in result.stdout
