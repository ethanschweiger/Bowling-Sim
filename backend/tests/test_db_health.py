"""`app.db.health.sql_database_is_reachable`: the one connectivity probe
`GET /health` uses in `"sql"` storage mode.

Offline only, like every other test in this persistence-infrastructure
arc -- no test here reaches a real Postgres. The "reachable" case uses a
genuine SQLite in-memory engine (a real database connection and a real
executed `SELECT 1`, just not Postgres) rather than mocking the SQL layer
away entirely; the "unreachable" case uses the same deliberately
unroutable URL pattern `test_db_session.py`/`test_api_game_service_storage_mode.py`
already establish, so a test that accidentally tried to really connect
would fail fast and obviously, not hang.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.health import sql_database_is_reachable
from app.db.session import build_engine, build_session_factory

# Same pattern as test_db_session.py/test_api_game_service_storage_mode.py:
# port 1 on localhost refuses connections immediately rather than hanging.
UNROUTABLE_URL = "postgresql+psycopg2://probe_user:probe_pass@localhost:1/probe_db"


def test_true_for_a_genuinely_reachable_database():
    engine = create_engine("sqlite:///:memory:")
    factory = sessionmaker(bind=engine)

    assert sql_database_is_reachable(factory) is True


def test_false_for_an_unroutable_database_without_raising():
    engine = build_engine(UNROUTABLE_URL)
    factory = build_session_factory(engine)

    assert sql_database_is_reachable(factory) is False
