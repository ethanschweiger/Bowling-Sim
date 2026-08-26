"""`app.db.session`: the opt-in SQLAlchemy `Engine`/`Session` factory.
Offline only, like `test_db_schema.py`/`test_db_row_store.py` -- no test
here opens a live database connection. `build_engine` is exercised
against deliberately unroutable URLs throughout, so a test that
accidentally tried to use the returned `Engine` for a real query would
fail loudly and immediately, not hang or silently succeed against a real
database.
"""

import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.session import build_engine, build_session_factory
from app.games.service import GameService, InMemoryGameSessionRepository, default_game_service

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Deliberately unroutable: port 1 on localhost refuses connections
# immediately rather than hanging, so any test that accidentally tried
# to actually use the resulting Engine would fail fast and obviously.
UNROUTABLE_URL = "postgresql+psycopg2://probe_user:probe_pass@localhost:1/probe_db"


def test_build_engine_defaults_to_settings_database_url():
    engine = build_engine()
    assert engine.url.render_as_string(hide_password=False) == settings.database_url


def test_build_engine_accepts_an_explicit_url_overriding_settings():
    engine = build_engine(UNROUTABLE_URL)
    assert engine.url.render_as_string(hide_password=False) == UNROUTABLE_URL
    assert engine.url.render_as_string(hide_password=False) != settings.database_url


def test_build_engine_passes_credentials_through_unmodified():
    """Not redacted, rewritten, or hardcoded -- the real username/password
    a caller passed in are exactly what the resulting Engine's URL holds.
    SQLAlchemy's own str(url)/repr redact the password for display
    (proven separately below); that's a display-only concern of
    SQLAlchemy's, not something this module does to the credentials
    themselves.
    """
    engine = build_engine(UNROUTABLE_URL)
    assert engine.url.username == "probe_user"
    assert engine.url.password == "probe_pass"
    # The redaction some SQLAlchemy call sites (like this test file's own
    # docstring warns about) show is display-only, e.g. in str(url) --
    # asserted here so a future reader isn't confused by seeing "***"
    # elsewhere and wondering if this module caused it.
    assert "***" in str(engine.url)
    assert "probe_pass" not in str(engine.url)


def test_build_session_factory_is_bound_to_the_given_engine():
    engine = build_engine(UNROUTABLE_URL)
    factory = build_session_factory(engine)

    assert isinstance(factory, sessionmaker)
    assert factory.kw["bind"] is engine


def test_build_session_factory_does_not_open_a_connection():
    """Constructing a sessionmaker is pure object wiring -- it must
    succeed instantly against an engine whose URL would fail immediately
    if anything actually tried to connect through it."""
    engine = build_engine(UNROUTABLE_URL)
    factory = build_session_factory(engine)
    assert factory is not None
    # A Session made from this factory is still just an unopened object
    # at this point -- constructing it is not "using" it.
    session = factory()
    try:
        assert isinstance(session, Session)
        assert session.bind is engine
    finally:
        session.close()


def test_game_service_and_default_game_service_still_use_in_memory_repository():
    """This module's whole reason to stay unwired: adding an opt-in
    factory must not switch runtime game storage away from
    InMemoryGameSessionRepository."""
    assert isinstance(GameService()._repository, InMemoryGameSessionRepository)
    assert isinstance(default_game_service._repository, InMemoryGameSessionRepository)


def test_importing_app_main_and_the_session_module_never_creates_an_engine():
    """A true process-isolation check, the same pattern
    test_db_schema.py's test_alembic_env_never_imports_app_main_in_a_fresh_process
    uses -- sabotages sqlalchemy.create_engine (before app.db.session's own
    `from sqlalchemy import create_engine` line ever runs, so the sabotage
    is what gets bound) so calling it during either import would raise
    loudly, then imports app.main and app.db.session in that order and
    confirms neither import triggered it.
    """
    probe = (
        "import sqlalchemy\n"
        "calls = []\n"
        "def _sabotaged_create_engine(*a, **k):\n"
        "    calls.append((a, k))\n"
        "    raise AssertionError('create_engine was called during import')\n"
        "sqlalchemy.create_engine = _sabotaged_create_engine\n"
        "import app.main\n"
        "import app.db.session\n"
        "assert calls == [], f'create_engine was called during import: {calls}'\n"
        "print('PROBE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PROBE_OK" in result.stdout
