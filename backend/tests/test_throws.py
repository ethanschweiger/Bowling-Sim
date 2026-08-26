from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from app.api.routes.games import TRUNCATED_TRAJECTORY_DETAIL
from app.api.routes.throws import LEGACY_GAME_ID
from app.games.service import (
    GameService,
    GameSessionRepository,
    InMemoryGameSessionRepository,
    default_game_service,
)
from app.main import app
from app.physics.simulate import SimulationResult, TerminalState, TrajectoryPoint
from app.physics.throw import Throw, sample_release

client = TestClient(app)

VALID_PAYLOAD = {
    "ball_id": "reactive_pearl",
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": 0.5,
    "launch_position": 28.0,
}


@pytest.fixture(autouse=True)
def reset_lane():
    """Each test starts from a fresh house shot. The deprecated legacy
    route shares one game (LEGACY_GAME_ID) across every call, lazily
    created on first use, which would otherwise carry wear between tests."""
    session = default_game_service.get_or_create(LEGACY_GAME_ID)
    session.reset()
    yield
    session.reset()


def test_unknown_ball_id_returns_404():
    payload = {**VALID_PAYLOAD, "ball_id": "not_a_real_ball"}
    response = client.post("/api/v1/simulations/throws", json=payload)
    assert response.status_code == 404


def test_speed_out_of_range_returns_422():
    payload = {**VALID_PAYLOAD, "speed_mph": 999.0}
    response = client.post("/api/v1/simulations/throws", json=payload)
    assert response.status_code == 422


def test_missing_required_field_returns_422():
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "ball_id"}
    response = client.post("/api/v1/simulations/throws", json=payload)
    assert response.status_code == 422


def _truncated_simulate_throw(ball, throw, lane_condition, step_ft=None):
    """A `simulate_throw`-shaped stand-in whose result never reaches the
    pin deck — see the identical helper in test_games_api.py for why."""
    return SimulationResult(
        path=[TrajectoryPoint(distance_ft=0.0, board=throw.launch_position)],
        entry_board=throw.launch_position,
        entry_angle_deg=0.0,
        speed_at_pins_mph=10.0,
        lane_condition_version=lane_condition.version,
        terminal=TerminalState(
            distance_ft=10.0,
            board=throw.launch_position,
            heading_deg=0.0,
            speed_mph=10.0,
            reached_pin_deck=False,
        ),
    )


def test_truncated_trajectory_returns_503_and_leaves_the_shared_game_unchanged(monkeypatch):
    before = client.get(f"/api/v1/games/{LEGACY_GAME_ID}").json()

    # Patch the name this route resolves it through, not `throws.py` — the
    # legacy route shares `create_game_throw`'s underlying mechanism only
    # via `GameSession.throw`, but calls its own module-level `simulate_throw`.
    monkeypatch.setattr("app.api.routes.throws.simulate_throw", _truncated_simulate_throw)

    response = client.post("/api/v1/simulations/throws", json=VALID_PAYLOAD)

    assert response.status_code == 503
    assert response.json()["detail"] == TRUNCATED_TRAJECTORY_DETAIL

    after = client.get(f"/api/v1/games/{LEGACY_GAME_ID}").json()
    assert after == before, (
        "a rejected legacy throw must not change lane version, rack, or scorecard"
    )


def test_representative_throw_returns_a_plausible_trajectory():
    payload = {**VALID_PAYLOAD, "seed": 7}
    response = client.post("/api/v1/simulations/throws", json=payload)
    assert response.status_code == 200

    body = response.json()

    # The path starts at the foul line and ends at (or just past) the pin deck.
    assert body["path"][0]["distance_ft"] == 0.0
    assert body["path"][-1]["distance_ft"] >= 59.0

    # Distance is strictly increasing — the ball never travels backward.
    distances = [point["distance_ft"] for point in body["path"]]
    assert distances == sorted(distances)

    # The ball stays on the lane the whole way down.
    for point in body["path"]:
        assert 0.0 <= point["board"] <= 40.0

    assert 0 <= body["pins_knocked"] <= 10
    assert body["seed"] == 7
    assert body["lane_condition_version"] == 1
    assert set(body["actual_release"].keys()) == {
        "speed_mph",
        "rev_rate",
        "axis_rotation",
        "axis_tilt",
        "launch_angle",
        "launch_position",
    }


def test_omitted_seed_is_generated_and_returned():
    response = client.post("/api/v1/simulations/throws", json=VALID_PAYLOAD)
    assert response.status_code == 200
    assert isinstance(response.json()["seed"], int)


def test_returned_actual_release_matches_the_seeded_trajectory_input():
    """The API must expose the sampled release that actually drove its path."""
    seed = 91
    response = client.post("/api/v1/simulations/throws", json={**VALID_PAYLOAD, "seed": seed})
    assert response.status_code == 200

    requested = Throw(**{key: value for key, value in VALID_PAYLOAD.items() if key != "ball_id"})
    expected, _ = sample_release(requested, seed)
    assert response.json()["actual_release"] == asdict(expected)


def test_consecutive_throws_advance_the_lane_condition_version():
    first = client.post("/api/v1/simulations/throws", json={**VALID_PAYLOAD, "seed": 1}).json()
    second = client.post("/api/v1/simulations/throws", json={**VALID_PAYLOAD, "seed": 2}).json()
    assert second["lane_condition_version"] == first["lane_condition_version"] + 1


def test_legacy_throw_response_uses_the_planar_collision_model():
    body = client.post("/api/v1/simulations/throws", json={**VALID_PAYLOAD, "seed": 7}).json()

    assert body["pinfall"]["model_id"] == "planar-collision-2d-v1"
    assert isinstance(body["pinfall"]["fallen_pin_ids"], list)
    assert body["pins_knocked"] == len(body["pinfall"]["fallen_pin_ids"])


class _SpyRepository(GameSessionRepository):
    """See tests/test_game_service_writeback.py's identical helper --
    wraps a real repository, delegating every call, while recording each
    `game_id` a `put()` call was made for."""

    def __init__(self, inner):
        self._inner = inner
        self.put_calls = []

    def get(self, game_id):
        return self._inner.get(game_id)

    def put(self, session):
        self.put_calls.append(session.game_id)
        self._inner.put(session)

    def get_or_put(self, game_id, factory):
        return self._inner.get_or_put(game_id, factory)


def test_legacy_throw_route_writes_back_through_the_same_service_path(monkeypatch):
    """Proves the deprecated route uses GameService.throw_in_game -- the
    same write-back path the game-scoped route uses -- not a direct
    session.throw() call that would silently skip a future persistent
    repository's put(). get_or_create still ensures the shared game
    exists; this only checks what happens after."""
    import app.api.routes.throws as throws_route

    spy = _SpyRepository(InMemoryGameSessionRepository())
    temp_service = GameService(repository=spy)
    monkeypatch.setattr(throws_route, "default_game_service", temp_service)

    response = client.post("/api/v1/simulations/throws", json=VALID_PAYLOAD)

    assert response.status_code == 200
    assert spy.put_calls == [LEGACY_GAME_ID]
