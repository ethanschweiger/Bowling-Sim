"""`app.api.dependencies.get_game_service`: the FastAPI dependency every
game-scoped and legacy route resolves its `GameService` through, instead
of reaching for `app.games.service.default_game_service` directly inside
a handler body. See that module's own docstring for the full design.

Uses `app.dependency_overrides` -- the intended, idiomatic FastAPI
mechanism for substituting a dependency in tests -- rather than
monkeypatching any route module's own globals. Every override is set
through `monkeypatch.setitem(app.dependency_overrides, ...)`, so pytest
cleans it up automatically at teardown (restoring "no override" the same
way it would restore any other dict entry) and a leaked override can
never bleed into another test file sharing this same `app` instance.
"""

from fastapi.testclient import TestClient

from app.api.dependencies import get_game_service
from app.api.routes.throws import LEGACY_GAME_ID
from app.games.service import GameService, InMemoryGameSessionRepository, default_game_service
from app.main import app

client = TestClient(app)

THROW_PAYLOAD = {
    "ball_id": "reactive_pearl",
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": 0.5,
    "launch_position": 28.0,
}


def test_default_provider_returns_default_game_service():
    assert get_game_service() is default_game_service


def test_game_scoped_routes_use_the_overridden_service_not_the_real_default(monkeypatch):
    """create/get/throw/reset all resolve their GameService through the
    dependency -- overriding it redirects every one of them, and none of
    it ever touches the real default_game_service."""
    test_service = GameService()
    monkeypatch.setitem(app.dependency_overrides, get_game_service, lambda: test_service)

    created = client.post("/api/v1/games", json={})
    assert created.status_code == 201
    game_id = created.json()["game_id"]

    # It's genuinely in the override's own service...
    assert test_service.get_game(game_id) is not None
    # ...and genuinely absent from the real default_game_service, not
    # just "also present because it's the same object under the hood."
    assert game_id not in default_game_service._repository._games

    throw_response = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    assert throw_response.status_code == 200

    status = client.get(f"/api/v1/games/{game_id}")
    assert status.status_code == 200
    assert status.json()["lane_condition_version"] == 2  # one throw's wear

    reset_response = client.post(f"/api/v1/games/{game_id}/reset")
    assert reset_response.status_code == 200
    assert reset_response.json()["lane_condition_version"] == 1

    assert game_id not in default_game_service._repository._games


def test_legacy_throw_route_uses_the_same_dependency_and_keeps_legacy_default_behavior(
    monkeypatch,
):
    """The deprecated route resolves its GameService through the exact
    same get_game_service dependency -- overriding it redirects
    LEGACY_GAME_ID into the override's own service, and the route's own
    lazily-created-shared-game behavior (get_or_create) still works
    against that substituted service.

    Compares default_game_service's own LEGACY_GAME_ID entry by identity
    before and after, rather than asserting it's absent: other test files
    in this suite (test_throws.py's reset_lane fixture) legitimately call
    default_game_service.get_or_create(LEGACY_GAME_ID) too, so that entry
    may already exist before this test ever runs, depending on file/test
    order within one pytest process -- combining files in a specific
    order (as the project's own required verification commands do) can
    make that happen before this test does. Whether it exists already or
    not, the property this test actually needs to prove is that this
    request left it completely untouched: identical object identity
    before and after proves exactly that, in either case. Asserting
    absence outright was never a guarantee the override working
    correctly could make on its own -- it depended on default_game_service
    never having been touched by anything else in the same process,
    which this suite doesn't promise.
    """
    test_service = GameService()
    monkeypatch.setitem(app.dependency_overrides, get_game_service, lambda: test_service)

    real_session_before = default_game_service._repository.get(LEGACY_GAME_ID)

    response = client.post("/api/v1/simulations/throws", json=THROW_PAYLOAD)
    assert response.status_code == 200

    assert test_service.get_game(LEGACY_GAME_ID) is not None
    # Whatever the real default_game_service held for LEGACY_GAME_ID
    # before this request (None, or some earlier test's own session) is
    # exactly what it still holds after -- this request never touched it.
    assert default_game_service._repository.get(LEGACY_GAME_ID) is real_session_before


def test_game_service_and_default_game_service_still_use_in_memory_repository():
    assert isinstance(GameService()._repository, InMemoryGameSessionRepository)
    assert isinstance(default_game_service._repository, InMemoryGameSessionRepository)


def test_importing_the_dependency_module_and_app_main_does_not_create_an_engine():
    """Same process-isolation pattern used throughout the persistence
    arc's own test files (test_db_schema.py, test_db_session.py,
    test_db_sql_repository.py) for this exact claim -- sabotages
    sqlalchemy.create_engine before either module is imported, then
    imports app.main and app.api.dependencies and confirms neither
    import triggered it. app.api.dependencies imports only
    app.games.service -- never app.db or app.db.sql_repository at all.
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
