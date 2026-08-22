from fastapi.testclient import TestClient

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


def _create_game():
    response = client.post("/api/v1/games", json={})
    assert response.status_code == 201
    return response.json()


def test_create_game_returns_id_and_initial_version():
    body = _create_game()
    assert isinstance(body["game_id"], str) and body["game_id"]
    assert body["lane_condition_version"] == 1


def test_unsupported_oil_pattern_is_rejected():
    response = client.post("/api/v1/games", json={"oil_pattern": "sport"})
    assert response.status_code == 422  # not yet a supported pattern


def test_unknown_game_id_returns_404_for_throws_and_reset():
    throw_response = client.post("/api/v1/games/does-not-exist/throws", json=THROW_PAYLOAD)
    assert throw_response.status_code == 404

    reset_response = client.post("/api/v1/games/does-not-exist/reset")
    assert reset_response.status_code == 404


def test_two_games_wear_independently_through_the_api():
    game_a = _create_game()
    game_b = _create_game()

    first = client.post(f"/api/v1/games/{game_a['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1}).json()
    assert first["game_id"] == game_a["game_id"]
    assert first["lane_condition_version"] == 1

    second_a = client.post(f"/api/v1/games/{game_a['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 2}).json()
    assert second_a["lane_condition_version"] == 2

    # game_b never had a throw — still at its initial version.
    reset_b = client.post(f"/api/v1/games/{game_b['game_id']}/reset").json()
    assert reset_b["lane_condition_version"] == 1


def test_reset_returns_game_to_version_1():
    game = _create_game()
    client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1})
    client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 2})

    reset_body = client.post(f"/api/v1/games/{game['game_id']}/reset").json()
    assert reset_body["game_id"] == game["game_id"]
    assert reset_body["lane_condition_version"] == 1

    after_reset = client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1}).json()
    assert after_reset["lane_condition_version"] == 1
