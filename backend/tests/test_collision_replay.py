"""Bounded, deterministic collision replay.

The central invariant: recording a replay is *passive observation*. The
same run, recorded and not recorded, must produce byte-identical fallen
IDs and step counts — if recording could perturb the solver, replay data
would be describing a different collision than the one that was scored.
"""

import pytest

from app.physics.collision import (
    DEFAULT_PINFALL_MODEL,
    MAX_COLLISION_STEPS,
    _simulate_collision_detail,
    simulate_collision,
)
from app.physics.impact import ImpactState
from app.physics.pin_deck import GUTTER_ABS_LATERAL_IN
from app.physics.rack import RackError
from app.physics.replay import (
    BALL_BODY_ID,
    MAX_REPLAY_FRAMES,
    REPLAY_MODEL_VERSION,
    REPLAY_SAMPLE_EVERY_STEPS,
)


def _impact(lateral_position_in, heading_deg, speed_mph=17.0):
    return ImpactState(
        lateral_position_in=lateral_position_in,
        heading_deg=heading_deg,
        speed_mph=speed_mph,
        ball_mass_lbs=15.0,
        ball_radius_in=4.29,
        lane_condition_version=1,
    )


POCKET = _impact(-2.6, 1.4)
LIGHT_HIT = _impact(0.0, 0.0)
BROOKLYN = _impact(3.0, -2.0)


# --- Recording never changes the physics ---------------------------------


@pytest.mark.parametrize(
    "label,impact,standing_ids",
    [
        ("pocket", POCKET, None),
        ("light hit", LIGHT_HIT, None),
        ("brooklyn", BROOKLYN, None),
        ("partial rack / spare", POCKET, {1, 3, 5}),
        ("single pin", POCKET, {10}),
    ],
)
def test_recording_a_replay_does_not_change_the_outcome(label, impact, standing_ids):
    plain = simulate_collision(impact, standing_ids=standing_ids)
    recorded = _simulate_collision_detail(
        impact, standing_ids=standing_ids, record_replay=True
    )

    assert recorded.fallen_pin_ids == plain[0], label
    assert recorded.steps_taken == plain[1], label
    assert recorded.replay is not None, label


def test_replay_is_byte_equivalent_across_identical_reruns():
    first = _simulate_collision_detail(POCKET, standing_ids={1, 3, 5}, record_replay=True)
    second = _simulate_collision_detail(POCKET, standing_ids={1, 3, 5}, record_replay=True)

    # Frozen dataclasses all the way down, so equality here is a genuine
    # structural comparison of every frame, body, and coordinate.
    assert first.replay == second.replay
    assert first.fallen_pin_ids == second.fallen_pin_ids


# --- Frame structure -----------------------------------------------------


def test_frames_are_bounded_and_stamped_with_the_model_version():
    replay = _simulate_collision_detail(POCKET, record_replay=True).replay

    assert replay.model_version == REPLAY_MODEL_VERSION
    assert replay.sample_every_steps == REPLAY_SAMPLE_EVERY_STEPS
    assert 0 < len(replay.frames) <= MAX_REPLAY_FRAMES
    # Bounded *well* below the solver's own iteration count -- the whole
    # point is not emitting a step flood.
    assert len(replay.frames) < MAX_COLLISION_STEPS / 10


def test_timestamps_increase_strictly_and_bracket_the_run():
    replay = _simulate_collision_detail(POCKET, record_replay=True).replay
    times = [frame.t_s for frame in replay.frames]

    assert times[0] == 0.0, "the first frame is the initial placement, before any stepping"
    assert times == sorted(times)
    assert len(set(times)) == len(times), "no duplicate timestamps"
    assert times[-1] == pytest.approx(replay.steps_taken * replay.dt_s)


def test_every_frame_carries_the_same_sorted_unique_participating_bodies():
    standing = {1, 3, 5}
    replay = _simulate_collision_detail(POCKET, standing_ids=standing, record_replay=True).replay

    expected_ids = [BALL_BODY_ID] + sorted(standing)
    for frame in replay.frames:
        ids = [body.body_id for body in frame.bodies]
        assert ids == expected_ids, "ids must be sorted, unique, and exactly the participants"


def test_no_body_appears_that_was_not_standing():
    replay = _simulate_collision_detail(POCKET, standing_ids={7}, record_replay=True).replay
    for frame in replay.frames:
        for body in frame.bodies:
            assert body.body_id in (BALL_BODY_ID, 7)


def test_coordinates_stay_finite_and_physically_bounded():
    replay = _simulate_collision_detail(POCKET, record_replay=True).replay
    for frame in replay.frames:
        for body in frame.bodies:
            assert body.x_in == body.x_in and body.y_in == body.y_in  # not NaN
            assert abs(body.x_in) != float("inf")
            assert abs(body.y_in) != float("inf")
            # Generous but real: nothing should fly off the deck entirely
            # in the ~2 s a run covers.
            assert abs(body.x_in) < 500.0
            assert abs(body.y_in) < 500.0


def test_initial_frame_places_bodies_at_their_starting_spots():
    replay = _simulate_collision_detail(POCKET, record_replay=True).replay
    first = replay.frames[0]

    ball = next(b for b in first.bodies if b.body_id == BALL_BODY_ID)
    assert ball.x_in == pytest.approx(POCKET.lateral_position_in)
    assert ball.y_in == pytest.approx(0.0), "the ball starts on the headpin plane, y=0"

    # Pins start downlane of the headpin plane (y >= 0), never behind it.
    for body in first.bodies:
        if body.body_id != BALL_BODY_ID:
            assert body.y_in >= 0.0


def test_final_frame_differs_from_the_initial_one_for_a_real_collision():
    # Guards against a replay that technically has frames but never
    # actually recorded any motion.
    replay = _simulate_collision_detail(POCKET, record_replay=True).replay
    assert replay.frames[0] != replay.frames[-1]


# --- No run means no replay, not an invented one -------------------------


def test_zero_speed_produces_no_run_and_no_replay():
    run = _simulate_collision_detail(_impact(-2.6, 1.4, speed_mph=0.0), record_replay=True)
    assert run.fallen_pin_ids == ()
    assert run.steps_taken == 0
    assert run.replay is None, "a collision that never happened must not have a replay"


def test_empty_rack_produces_no_run_and_no_replay():
    run = _simulate_collision_detail(POCKET, standing_ids=set(), record_replay=True)
    assert run.fallen_pin_ids == ()
    assert run.steps_taken == 0
    assert run.replay is None


def test_gutter_ball_resolves_with_no_replay():
    gutter = _impact(GUTTER_ABS_LATERAL_IN + 1.0, 0.0)
    result = DEFAULT_PINFALL_MODEL.resolve(gutter)
    assert result.pins_knocked == 0
    assert result.fallen_pin_ids == ()
    assert result.replay is None


def test_an_invalid_rack_raises_before_any_replay_is_emitted():
    # Lists, not set literals: `{1, 1.0}` collapses to `{1}` (they compare
    # equal and hash alike), which would silently make that case *valid*
    # and the assertion vacuous. A list preserves the float that must be
    # rejected.
    for bad in ([11], [0], [1, 1.0], [True], [1, 1]):
        with pytest.raises(RackError):
            _simulate_collision_detail(POCKET, standing_ids=bad, record_replay=True)
        # And through the model's own entry point, which validates
        # independently rather than trusting the solver to.
        with pytest.raises(RackError):
            DEFAULT_PINFALL_MODEL.resolve(POCKET, standing_ids=bad)


# --- The model surfaces it, without disturbing existing fields -----------


def test_planar_model_exposes_replay_agreeing_with_its_own_fallen_ids():
    result = DEFAULT_PINFALL_MODEL.resolve(POCKET)

    assert result.replay is not None
    assert result.pins_knocked == len(result.fallen_pin_ids)

    # Every pin the model reports as fallen must be a body the replay
    # actually simulated -- the animation and the score describe one run.
    replay_ids = {b.body_id for b in result.replay.frames[0].bodies}
    for pin_id in result.fallen_pin_ids:
        assert pin_id in replay_ids


def test_heuristic_model_reports_no_replay():
    from app.physics.pinfall import EntryAngleHeuristicPinfallModel

    result = EntryAngleHeuristicPinfallModel().resolve(POCKET)
    # It resolves a pin count by formula, with no bodies to animate.
    assert result.replay is None
    assert result.fallen_pin_ids == ()
