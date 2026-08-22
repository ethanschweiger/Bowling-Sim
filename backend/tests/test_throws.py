import pytest
from fastapi.testclient import TestClient

from app.api.routes.throws import LEGACY_GAME_ID
from app.games.service import default_game_service
from app.main import app

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


def test_consecutive_throws_advance_the_lane_condition_version():
    first = client.post("/api/v1/simulations/throws", json={**VALID_PAYLOAD, "seed": 1}).json()
    second = client.post("/api/v1/simulations/throws", json={**VALID_PAYLOAD, "seed": 2}).json()
    assert second["lane_condition_version"] == first["lane_condition_version"] + 1
