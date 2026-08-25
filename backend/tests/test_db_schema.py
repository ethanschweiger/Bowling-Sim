"""`app.db.schema` (the `game_sessions` table metadata) and the initial
Alembic migration that creates it. Offline only, everywhere in this file —
no test here opens a live database connection, the same constraint the
active task itself places on the scaffold. See `app/db/schema.py` and
`alembic/versions/a42df50c3040_create_game_sessions_table.py` for what
this exercises, and `test_game_session_record_payload.py` for the
`record_to_payload()` shape this table's `payload` column is meant to
hold (unread by any runtime code today — nothing here changes that).
"""

import contextlib
import io
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB

from app.db.schema import game_sessions, metadata
from app.games.service import GameService, InMemoryGameSessionRepository, default_game_service

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def test_metadata_declares_exactly_the_game_sessions_table():
    assert set(metadata.tables) == {"game_sessions"}
    assert metadata.tables["game_sessions"] is game_sessions


def test_game_sessions_table_has_the_required_columns_and_types():
    columns = {column.name: column for column in game_sessions.columns}
    assert set(columns) == {"game_id", "payload", "payload_version", "created_at", "updated_at"}

    assert isinstance(columns["game_id"].type, sa.String)
    assert columns["game_id"].nullable is False

    assert isinstance(columns["payload"].type, JSONB)
    assert columns["payload"].nullable is False

    assert isinstance(columns["payload_version"].type, sa.Integer)
    assert columns["payload_version"].nullable is False

    for name in ("created_at", "updated_at"):
        assert isinstance(columns[name].type, sa.DateTime)
        assert columns[name].type.timezone is True
        assert columns[name].nullable is False


def test_game_id_is_the_sole_primary_key():
    pk_columns = [column.name for column in game_sessions.primary_key.columns]
    assert pk_columns == ["game_id"]


def test_offline_upgrade_sql_creates_the_expected_table():
    """Exercises the real migration file's upgrade() -- the same
    `alembic upgrade head --sql` path the active task's own required
    verification runs, captured here instead of only eyeballed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        upgrade(_alembic_config(), "head", sql=True)
    rendered_sql = buf.getvalue()

    assert "CREATE TABLE game_sessions" in rendered_sql
    assert "game_id VARCHAR NOT NULL" in rendered_sql
    assert "payload JSONB NOT NULL" in rendered_sql
    assert "payload_version INTEGER DEFAULT '1' NOT NULL" in rendered_sql
    assert "PRIMARY KEY (game_id)" in rendered_sql


def test_offline_downgrade_sql_drops_the_table():
    cfg = _alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None  # there must be a migration to downgrade from

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        downgrade(cfg, f"{head}:base", sql=True)
    rendered_sql = buf.getvalue()

    assert "DROP TABLE game_sessions" in rendered_sql


def test_default_game_service_still_uses_the_in_memory_repository():
    """The active task's own scope boundary: adding a migration scaffold
    must not switch runtime game storage away from
    InMemoryGameSessionRepository -- nothing reads or writes through
    app.db.schema yet."""
    assert isinstance(default_game_service._repository, InMemoryGameSessionRepository)


def test_a_freshly_constructed_game_service_still_defaults_to_in_memory_too():
    service = GameService()
    assert isinstance(service._repository, InMemoryGameSessionRepository)


def test_alembic_env_never_imports_app_main_in_a_fresh_process():
    """`app.main` is what actually builds the FastAPI app, installs CORS
    middleware, and registers routes -- alembic/env.py must reach
    app.db.schema's metadata without ever touching it.

    A true process-isolation check, not a `sys.modules` inspection in
    this same test process: other files in this suite already import
    `app.main` for their own FastAPI `TestClient`, so `sys.modules`
    would already contain it regardless of what Alembic does once the
    full suite has run -- an in-process check would only mean anything
    run in total isolation. This spawns a fresh interpreter, sabotages
    `app.main` so importing it would loudly fail, and then runs the real
    offline upgrade path in that fresh process.
    """
    probe = (
        "import sys, types\n"
        "sentinel = types.ModuleType('app.main')\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('app.main was imported during Alembic env load')\n"
        "sentinel.__getattr__ = _boom\n"
        "sys.modules['app.main'] = sentinel\n"
        "from alembic.config import Config\n"
        "from alembic.command import upgrade\n"
        "upgrade(Config('alembic.ini'), 'head', sql=True)\n"
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
