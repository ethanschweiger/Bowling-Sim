from fastapi.testclient import TestClient

from app.api.routes.games import TRUNCATED_TRAJECTORY_DETAIL
from app.games.service import default_game_service
from app.main import app
from app.physics.ball import BALL_CATALOG
from app.physics.pinfall import PinfallResult
from app.physics.simulate import SimulationResult, TerminalState, TrajectoryPoint, simulate_throw
from app.physics.throw import Throw

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


def _truncated_simulate_throw(ball, throw, lane_condition, step_ft=None):
    """A `simulate_throw`-shaped stand-in whose result never reaches the pin
    deck — deterministic, no monkeypatched global stride, no need to hunt
    for a real release that happens to fail."""
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


def test_truncated_trajectory_returns_503_and_leaves_game_state_unchanged(monkeypatch):
    game = _create_game()
    game_id = game["game_id"]
    before = client.get(f"/api/v1/games/{game_id}").json()

    # Patch the name the route actually calls, not the module it's defined
    # in — `create_game_throw` resolves `simulate_throw` from
    # `app.api.routes.games`'s own globals at call time.
    monkeypatch.setattr("app.api.routes.games.simulate_throw", _truncated_simulate_throw)

    response = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)

    assert response.status_code == 503
    body = response.json()
    # Exact match, not a substring check: proves nothing else — no stack
    # trace, no raw distance/stride figure from the domain exception's own
    # message — was appended to the stable text.
    assert body["detail"] == TRUNCATED_TRAJECTORY_DETAIL

    after = client.get(f"/api/v1/games/{game_id}").json()
    assert after == before, "a rejected throw must not change lane version, rack, or scorecard"


def test_truncated_trajectory_does_not_advance_the_scorecard_across_repeated_calls(monkeypatch):
    """Two rejected throws in a row must both be no-ops, not merely the first."""
    game = _create_game()
    game_id = game["game_id"]
    monkeypatch.setattr("app.api.routes.games.simulate_throw", _truncated_simulate_throw)

    first = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    second = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    assert first.status_code == second.status_code == 503

    status = client.get(f"/api/v1/games/{game_id}").json()
    assert status["lane_condition_version"] == 1
    assert status["game_state"]["frames"] == []
    assert status["game_state"]["total_score"] is None


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


def test_game_throw_response_uses_the_planar_collision_model():
    game = _create_game()
    body = client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 7}).json()

    assert body["pinfall"]["model_id"] == "planar-collision-2d-v1"
    assert isinstance(body["pinfall"]["fallen_pin_ids"], list)
    assert all(1 <= pin_id <= 10 for pin_id in body["pinfall"]["fallen_pin_ids"])
    assert len(set(body["pinfall"]["fallen_pin_ids"])) == len(body["pinfall"]["fallen_pin_ids"])
    assert body["pins_knocked"] == len(body["pinfall"]["fallen_pin_ids"])


def test_create_game_state_is_a_fresh_blank_scorecard_and_full_rack():
    body = _create_game()
    gs = body["game_state"]
    assert gs["standing_pin_ids"] == list(range(1, 11))
    assert gs["frames"] == []
    assert gs["total_score"] is None
    assert gs["is_game_complete"] is False
    assert gs["next_frame_number"] == 1
    assert gs["next_ball_number"] == 1


def test_throw_response_game_state_is_internally_consistent():
    game = _create_game()
    body = client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 3}).json()
    gs = body["game_state"]

    assert len(gs["frames"]) >= 1
    frame1 = gs["frames"][0]
    if frame1["is_complete"]:
        # Either a strike, or frame 1 finished on ball 2 — either way the
        # next ball (frame 2 or the tenth-frame equivalent) gets a fresh rack.
        assert len(gs["standing_pin_ids"]) == 10
    else:
        # Frame 1 still waiting on ball 2 — the rack reflects exactly what
        # ball 1 knocked down.
        assert len(gs["standing_pin_ids"]) == 10 - body["pins_knocked"]
    assert body["pins_knocked"] == sum(frame1["rolls"])


def test_reset_response_game_state_matches_a_fresh_game():
    game = _create_game()
    client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1})

    reset_body = client.post(f"/api/v1/games/{game['game_id']}/reset").json()
    gs = reset_body["game_state"]
    assert gs["standing_pin_ids"] == list(range(1, 11))
    assert gs["frames"] == []
    assert gs["is_game_complete"] is False
    assert reset_body["lane_condition_version"] == 1


def test_throw_after_game_completion_returns_409_and_reset_recovers():
    game = _create_game()
    game_id = game["game_id"]
    session = default_game_service.get_game(game_id)
    ball = BALL_CATALOG["house_ball"]
    throw = Throw()

    def strike_everything(condition):
        return simulate_throw(ball, throw, condition)

    def resolve_strike(sim_result, standing_ids):
        return PinfallResult(pins_knocked=10, model_id="t", limitations="", fallen_pin_ids=tuple(sorted(standing_ids)))

    for _ in range(12):
        session.throw(simulate=strike_everything, resolve_pinfall=resolve_strike)
    assert session.current_snapshot().is_game_complete

    lane_version_before = session.lane.condition.version

    response = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    assert response.status_code == 409
    assert session.lane.condition.version == lane_version_before  # rejected before anything changed

    reset_body = client.post(f"/api/v1/games/{game_id}/reset").json()
    assert reset_body["lane_condition_version"] == 1
    assert reset_body["game_state"]["is_game_complete"] is False
    assert reset_body["game_state"]["total_score"] is None

    # The game accepts throws again after reset.
    after_reset = client.post(f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 1})
    assert after_reset.status_code == 200


def test_get_unknown_game_returns_404():
    response = client.get("/api/v1/games/does-not-exist")
    assert response.status_code == 404


def test_get_fresh_game_matches_create_response():
    created = _create_game()
    game_id = created["game_id"]

    status = client.get(f"/api/v1/games/{game_id}").json()
    assert status["game_id"] == game_id
    assert status["lane_condition_version"] == created["lane_condition_version"]
    assert status["game_state"] == created["game_state"]


def test_get_after_a_throw_matches_the_throws_own_game_state():
    game = _create_game()
    game_id = game["game_id"]

    throw_body = client.post(f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 5}).json()
    status = client.get(f"/api/v1/games/{game_id}").json()

    # The throw response's lane_condition_version is (unchanged, documented
    # semantic) the version that throw ran *against* — GET reports the
    # current version, i.e. one *after* wear from that same throw applied.
    assert status["lane_condition_version"] == throw_body["lane_condition_version"] + 1
    # game_state itself (frames/rack/score) describes the same committed
    # state the throw's own response already reported — nothing else
    # touched this game between the two calls.
    assert status["game_state"] == throw_body["game_state"]


def test_get_after_reset_matches_a_fresh_game():
    game = _create_game()
    game_id = game["game_id"]
    client.post(f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 2})

    reset_body = client.post(f"/api/v1/games/{game_id}/reset").json()
    status = client.get(f"/api/v1/games/{game_id}").json()

    assert status["lane_condition_version"] == reset_body["lane_condition_version"] == 1
    assert status["game_state"] == reset_body["game_state"]
    assert status["game_state"]["standing_pin_ids"] == list(range(1, 11))
    assert status["game_state"]["frames"] == []
