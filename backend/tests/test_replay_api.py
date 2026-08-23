"""The serialized collision-replay contract, on both throw routes.

Additive by design: every field that existed before this replay data was
published still means exactly what it did. These tests check the new
`pinfall.replay` shape *and* that it agrees with the same response's
`fallen_pin_ids` and the game's own rack — a replay that disagreed with
the score would be animating a different collision than the one played.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.schemas import CollisionReplayResponse
from app.physics.collision import MAX_COLLISION_SECONDS, MAX_COLLISION_STEPS
from app.physics.replay import BALL_BODY_ID, MAX_REPLAY_FRAMES, REPLAY_MODEL_VERSION

client = TestClient(app)

THROW_PAYLOAD = {
    "ball_id": "reactive_pearl",
    "seed": 17,
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": -1.5,
    "launch_position": 28.0,
}

# A real, legal release that ends in the gutter — found by actually
# throwing it, not by monkeypatching a model, so the no-run path is
# exercised exactly as a client would hit it.
GUTTER_PAYLOAD = {
    **THROW_PAYLOAD,
    "ball_id": "house_ball",
    "launch_position": 1.0,
    "launch_angle": -2.0,
}


def _create_game():
    response = client.post("/api/v1/games", json={})
    assert response.status_code == 201
    return response.json()["game_id"]


def _throw(game_id, payload=None):
    response = client.post(f"/api/v1/games/{game_id}/throws", json=payload or THROW_PAYLOAD)
    assert response.status_code == 200
    return response.json()


# --- The serialized shape ------------------------------------------------


def test_game_throw_serializes_a_bounded_versioned_replay():
    body = _throw(_create_game())
    replay = body["pinfall"]["replay"]

    assert replay is not None
    assert replay["model_version"] == REPLAY_MODEL_VERSION
    assert replay["dt_s"] > 0
    assert replay["sample_every_steps"] > 0
    assert replay["steps_taken"] > 0
    assert 0 < len(replay["frames"]) <= MAX_REPLAY_FRAMES


def test_serialized_frames_have_strictly_increasing_timestamps_and_finite_positions():
    replay = _throw(_create_game())["pinfall"]["replay"]

    times = [frame["t_s"] for frame in replay["frames"]]
    assert times[0] == 0.0
    assert times == sorted(times)
    assert len(set(times)) == len(times)

    for frame in replay["frames"]:
        for body in frame["bodies"]:
            assert isinstance(body["body_id"], int)
            # JSON has no NaN/Infinity literal, so a non-finite value would
            # have failed serialization outright; this pins the magnitude.
            assert abs(body["x_in"]) < 500.0
            assert abs(body["y_in"]) < 500.0


def test_every_serialized_frame_carries_the_same_sorted_body_ids():
    replay = _throw(_create_game())["pinfall"]["replay"]

    first_ids = [b["body_id"] for b in replay["frames"][0]["bodies"]]
    assert first_ids == sorted(set(first_ids)), "sorted and unique"
    assert first_ids[0] == BALL_BODY_ID

    for frame in replay["frames"]:
        assert [b["body_id"] for b in frame["bodies"]] == first_ids


# --- Agreement with the rest of the same response ------------------------


def test_replay_bodies_agree_with_the_response_fallen_pin_ids():
    body = _throw(_create_game())
    replay_ids = {b["body_id"] for b in body["pinfall"]["replay"]["frames"][0]["bodies"]}

    assert body["pins_knocked"] == len(body["pinfall"]["fallen_pin_ids"])
    for pin_id in body["pinfall"]["fallen_pin_ids"]:
        assert pin_id in replay_ids, "a pin can't fall in a run that never simulated it"


def test_second_ball_replay_contains_only_the_pins_still_standing():
    """The rack-agreement invariant, end to end through the API."""
    game_id = _create_game()
    first = _throw(game_id, {**THROW_PAYLOAD, "ball_id": "house_ball", "seed": 3})
    standing_after_first = first["game_state"]["standing_pin_ids"]
    assert standing_after_first, "need a non-strike first ball for this to be meaningful"

    second = _throw(game_id, {**THROW_PAYLOAD, "ball_id": "house_ball", "seed": 4})
    replay = second["pinfall"]["replay"]
    assert replay is not None

    body_ids = [b["body_id"] for b in replay["frames"][0]["bodies"]]
    assert body_ids == [BALL_BODY_ID] + sorted(standing_after_first)


# --- Additive: nothing that existed before changed ------------------------


def test_existing_response_fields_are_unchanged_alongside_the_new_replay():
    body = _throw(_create_game())

    for field in (
        "seed",
        "actual_release",
        "path",
        "entry_board",
        "entry_angle_deg",
        "speed_at_pins_mph",
        "pins_knocked",
        "pinfall",
        "lane_condition_version",
        "game_state",
    ):
        assert field in body, field

    assert set(body["pinfall"]) == {"model_id", "limitations", "fallen_pin_ids", "replay"}
    assert body["pinfall"]["model_id"] == "planar-collision-2d-v1"
    assert isinstance(body["pins_knocked"], int)


# --- No run means a null replay, not a fabricated one --------------------


def test_a_gutter_throw_serializes_a_null_replay():
    body = _throw(_create_game(), GUTTER_PAYLOAD)

    assert body["pins_knocked"] == 0
    assert body["pinfall"]["fallen_pin_ids"] == []
    assert body["pinfall"]["replay"] is None, "no collision ran, so there is nothing to replay"
    # The rest of the response is still a complete, ordinary success.
    assert body["path"]
    assert body["game_state"]["frames"]


# --- The deprecated legacy route serializes the identical shape ----------


def test_legacy_route_serializes_the_same_replay_contract():
    # Reset first: this route shares one process-wide game, so an earlier
    # test's throws would otherwise leave a partial rack behind.
    client.post("/api/v1/games/legacy-default/reset")

    response = client.post("/api/v1/simulations/throws", json=THROW_PAYLOAD)
    assert response.status_code == 200
    replay = response.json()["pinfall"]["replay"]

    assert replay is not None
    assert replay["model_version"] == REPLAY_MODEL_VERSION
    assert 0 < len(replay["frames"]) <= MAX_REPLAY_FRAMES
    assert [b["body_id"] for b in replay["frames"][0]["bodies"]][0] == BALL_BODY_ID


def test_legacy_route_gutter_throw_also_serializes_a_null_replay():
    client.post("/api/v1/games/legacy-default/reset")

    response = client.post("/api/v1/simulations/throws", json=GUTTER_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["pins_knocked"] == 0
    assert body["pinfall"]["replay"] is None


# --- The heuristic model reports no replay through the same mapper -------


def test_heuristic_model_maps_to_a_null_replay():
    """The route mapper must not invent frames for a model that has none."""
    from app.api.routes.games import pinfall_to_response
    from app.physics.impact import ImpactState
    from app.physics.pinfall import EntryAngleHeuristicPinfallModel

    impact = ImpactState(
        lateral_position_in=-2.6,
        heading_deg=1.4,
        speed_mph=17.0,
        ball_mass_lbs=15.0,
        ball_radius_in=4.29,
        lane_condition_version=1,
    )
    result = EntryAngleHeuristicPinfallModel().resolve(impact)
    mapped = pinfall_to_response(result)

    assert mapped.replay is None
    assert mapped.model_id == "entry-angle-heuristic-v1"
    # It still reports a pin count; only the replay is absent.
    assert mapped.fallen_pin_ids == []


# --- Termination is published, on both routes ----------------------------


def test_game_throw_publishes_the_v3_model_and_a_valid_termination_reason():
    replay = _throw(_create_game())["pinfall"]["replay"]

    # The literal string, not the constant: a consumer keys its firewall off
    # this exact value, so the response must be pinned independently of
    # whatever the backend currently defines.
    assert replay["model_version"] == "planar-collision-replay-2d-v3"
    assert replay["termination_reason"] in ("settled", "step_cap")


def test_legacy_route_publishes_the_same_v3_termination_contract():
    client.post("/api/v1/games/legacy-default/reset")

    response = client.post("/api/v1/simulations/throws", json=THROW_PAYLOAD)
    assert response.status_code == 200
    replay = response.json()["pinfall"]["replay"]

    assert replay["model_version"] == "planar-collision-replay-2d-v3"
    assert replay["termination_reason"] in ("settled", "step_cap")


def test_a_seeded_full_rack_throw_reports_the_step_cap_it_actually_hits():
    # The representative case a browser sees: a normal legal release runs
    # the full 2 s without every body dropping under the settle threshold.
    replay = _throw(_create_game())["pinfall"]["replay"]

    assert replay["termination_reason"] == "step_cap"
    assert replay["steps_taken"] == MAX_COLLISION_STEPS
    assert replay["dt_s"] * replay["steps_taken"] == MAX_COLLISION_SECONDS


def test_the_serialized_reason_matches_the_domain_run_it_came_from():
    # Same impact through the mapper rather than the HTTP stack, so the
    # published value is checked against the solver's own recorded exit
    # instead of merely against the set of legal strings.
    from app.api.routes.games import pinfall_to_response
    from app.physics.collision import DEFAULT_PINFALL_MODEL
    from app.physics.impact import ImpactState

    impact = ImpactState(
        lateral_position_in=-2.6,
        heading_deg=1.4,
        speed_mph=17.0,
        ball_mass_lbs=15.0,
        ball_radius_in=4.29,
        lane_condition_version=1,
    )
    result = DEFAULT_PINFALL_MODEL.resolve(impact)
    mapped = pinfall_to_response(result)

    assert mapped.replay is not None
    assert mapped.replay.termination_reason == result.replay.termination_reason


def test_a_gutter_throw_still_has_no_replay_to_carry_a_reason():
    body = _throw(_create_game(), GUTTER_PAYLOAD)

    # The field is required *within* a replay; a run that never happened
    # has no replay at all, so nothing gains invented metadata.
    assert body["pinfall"]["replay"] is None


def test_the_response_model_refuses_an_unknown_or_missing_reason():
    # The Literal is the boundary guard: an unrecognized reason must fail
    # validation here rather than reach a client that would have to guess.
    valid = {
        "model_version": REPLAY_MODEL_VERSION,
        "dt_s": 0.0005,
        "sample_every_steps": 100,
        "steps_taken": 4000,
        "frames": [],
        "termination_reason": "step_cap",
    }
    assert CollisionReplayResponse(**valid).termination_reason == "step_cap"

    for bad in ("SETTLED", "settled ", "cap", "unknown", "", None):
        with pytest.raises(ValidationError):
            CollisionReplayResponse(**{**valid, "termination_reason": bad})

    with pytest.raises(ValidationError):
        CollisionReplayResponse(**{k: v for k, v in valid.items() if k != "termination_reason"})


# --- The v3 cadence survives serialization, on both routes ---------------


def _expected_frame_count(steps_taken, sample_every_steps):
    schedule = list(range(0, steps_taken + 1, sample_every_steps))
    if schedule[-1] != steps_taken:
        schedule.append(steps_taken)
    return len(schedule)


def test_game_throw_serializes_the_full_v3_schedule():
    replay = _throw(_create_game())["pinfall"]["replay"]

    assert replay["model_version"] == "planar-collision-replay-2d-v3"
    assert replay["sample_every_steps"] == 20
    assert replay["dt_s"] * replay["sample_every_steps"] == pytest.approx(0.01)
    # A normal seeded throw runs the full cap, so it carries every one of
    # the 201 scheduled frames -- none dropped in serialization.
    assert replay["steps_taken"] == MAX_COLLISION_STEPS
    assert len(replay["frames"]) == 201
    assert len(replay["frames"]) <= MAX_REPLAY_FRAMES

    times = [frame["t_s"] for frame in replay["frames"]]
    assert times[0] == 0.0
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    for index, time in enumerate(times):
        assert time == pytest.approx(index * 20 * replay["dt_s"], abs=1e-9)


def test_legacy_route_serializes_the_same_v3_schedule():
    client.post("/api/v1/games/legacy-default/reset")

    response = client.post("/api/v1/simulations/throws", json=THROW_PAYLOAD)
    assert response.status_code == 200
    replay = response.json()["pinfall"]["replay"]

    assert replay["model_version"] == "planar-collision-replay-2d-v3"
    assert replay["sample_every_steps"] == 20
    assert len(replay["frames"]) == 201
    assert len(replay["frames"]) == _expected_frame_count(
        replay["steps_taken"], replay["sample_every_steps"]
    )


def test_serialized_v3_bodies_stay_bounded_and_stable_across_every_frame():
    replay = _throw(_create_game())["pinfall"]["replay"]
    first_ids = [body["body_id"] for body in replay["frames"][0]["bodies"]]

    assert first_ids == sorted(first_ids)
    assert first_ids[0] == BALL_BODY_ID
    for frame in replay["frames"]:
        assert [body["body_id"] for body in frame["bodies"]] == first_ids
        for body in frame["bodies"]:
            # JSON has no NaN/Infinity literal, so serialization would have
            # failed outright on a non-finite value; this pins magnitude.
            assert abs(body["x_in"]) < 500.0
            assert abs(body["y_in"]) < 500.0


def test_the_serialized_replay_matches_the_domain_run_frame_for_frame():
    # Denser sampling is only useful if it survives the mapping intact, so
    # compare every published frame against the solver's own.
    from app.api.routes.games import pinfall_to_response
    from app.physics.collision import DEFAULT_PINFALL_MODEL
    from app.physics.impact import ImpactState

    impact = ImpactState(
        lateral_position_in=-2.6,
        heading_deg=1.4,
        speed_mph=17.0,
        ball_mass_lbs=15.0,
        ball_radius_in=4.29,
        lane_condition_version=1,
    )
    result = DEFAULT_PINFALL_MODEL.resolve(impact)
    mapped = pinfall_to_response(result)

    assert mapped.replay is not None
    assert len(mapped.replay.frames) == len(result.replay.frames) == 201
    # Indexed rather than zipped: `zip(strict=)` needs Python 3.10 and this
    # project's floor is 3.9, and the lengths are already asserted equal
    # above, so pairing by index is exact rather than merely truncating.
    for index in range(len(result.replay.frames)):
        published = mapped.replay.frames[index]
        recorded = result.replay.frames[index]

        assert published.t_s == recorded.t_s
        assert [b.body_id for b in published.bodies] == [b.body_id for b in recorded.bodies]
        for body_index in range(len(recorded.bodies)):
            assert published.bodies[body_index].x_in == recorded.bodies[body_index].x_in
            assert published.bodies[body_index].y_in == recorded.bodies[body_index].y_in
