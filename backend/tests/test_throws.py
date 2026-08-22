from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_PAYLOAD = {
    "ball_id": "reactive_pearl",
    "oil_pattern": "house",
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": 2.0,
    "launch_position": 28.0,
}


def test_unknown_ball_id_returns_404():
    payload = {**VALID_PAYLOAD, "ball_id": "not_a_real_ball"}
    response = client.post("/api/v1/simulations/throws", json=payload)
    assert response.status_code == 404


def test_unknown_oil_pattern_returns_404():
    payload = {**VALID_PAYLOAD, "oil_pattern": "not_a_real_pattern"}
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
    response = client.post("/api/v1/simulations/throws", json=VALID_PAYLOAD)
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
