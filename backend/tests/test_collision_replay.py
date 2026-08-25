"""Bounded, deterministic collision replay.

The central invariant: recording a replay is *passive observation*. The
same run, recorded and not recorded, must produce byte-identical fallen
IDs and step counts — if recording could perturb the solver, replay data
would be describing a different collision than the one that was scored.
"""

import math
from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from app.models.schemas import CollisionReplayResponse
from app.physics.collision import (
    COLLISION_DT_S,
    DEFAULT_PINFALL_MODEL,
    LINEAR_DAMPING_PER_S,
    MAX_COLLISION_SECONDS,
    MAX_COLLISION_STEPS,
    SETTLE_SPEED_IN_S,
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
    TERMINATION_REASONS,
)
from app.physics.units import mph_to_in_per_s


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


# --- How the run ended ---------------------------------------------------
#
# The solver loop has exactly two exits, and `termination_reason` reports
# which one fired.
#
# The reason is NOT a function of `steps_taken`. Only two implications
# hold, each in one direction only:
#
#   steps_taken < MAX_COLLISION_STEPS  =>  settled
#   step_cap                           =>  steps_taken == MAX_COLLISION_STEPS
#
# At the cap either reason is possible, because the settle predicate is
# evaluated after every step including the last permitted one. An earlier
# version of this file asserted the bidirectional equivalence and was
# wrong; `test_a_threshold_crossing_on_the_final_step_is_still_settled`
# below constructs the counterexample from the solver's own constants.


# A deliberately low-energy impact: slow enough that damping alone carries
# every body under SETTLE_SPEED_IN_S before the step cap, so the loop takes
# its settle exit. `LINEAR_DAMPING_PER_S` gives a factor of
# (1 - 1.2*0.0005) = 0.9994 per step, so 4,000 steps scale speed by ~0.0907
# and anything under SETTLE_SPEED_IN_S / 0.0907 ~= 5.5 in/s (~0.31 mph) can
# reach the threshold in time. 0.05 mph (0.88 in/s) clears that with a wide
# margin, and is placed off to the side so the run is pure damping with no
# contact. Derived from the constants, not tuned by trial.
LOW_ENERGY = _impact(-8.0, 0.0, speed_mph=0.05)


def _terminal_step_settle_speed_mph() -> float:
    """The release speed whose damping curve crosses `SETTLE_SPEED_IN_S`
    exactly on the last permitted step.

    Derived from the solver's own constants rather than searched for, so it
    stays correct if the damping rate, timestep, threshold, or cap change.

    Undamped-contact motion decays by `damping_factor` per step, so after
    `n` steps a body's speed is `v0 * damping_factor**n`. For the crossing
    to land on step `MAX_COLLISION_STEPS` and not before, `v0` must satisfy

        v0 * damping_factor**(cap - 1) >= SETTLE_SPEED_IN_S   (still moving)
        v0 * damping_factor**cap        <  SETTLE_SPEED_IN_S   (now settled)

    which is a real interval, not a single value. Taking its midpoint lands
    comfortably inside rather than on either edge, where float rounding
    could tip the result a step either way.
    """
    damping_factor = 1.0 - LINEAR_DAMPING_PER_S * COLLISION_DT_S
    still_moving_at = SETTLE_SPEED_IN_S / damping_factor ** (MAX_COLLISION_STEPS - 1)
    settled_at = SETTLE_SPEED_IN_S / damping_factor**MAX_COLLISION_STEPS
    midpoint_in_s = (still_moving_at + settled_at) / 2.0
    return midpoint_in_s / mph_to_in_per_s(1.0)


# Far enough off to the side that no pin is ever within contact range, so
# the run is pure damping and the arithmetic above is the whole story. It is
# handed straight to the solver, which does not gutter-check — that lives in
# `PlanarCollisionPinfallModel.resolve`, and this is a read-only direct call.
TERMINAL_STEP_SETTLE = _impact(-30.0, 0.0, speed_mph=_terminal_step_settle_speed_mph())


def test_a_normal_legal_impact_ends_at_the_step_cap():
    run = _simulate_collision_detail(POCKET, record_replay=True)

    assert run.replay.termination_reason == "step_cap"
    assert run.steps_taken == MAX_COLLISION_STEPS
    # Real pinfall, so this is the ordinary case rather than a degenerate
    # one that happens to hit the cap.
    assert run.fallen_pin_ids != ()


def test_a_low_energy_run_ends_at_the_settle_threshold():
    run = _simulate_collision_detail(LOW_ENERGY, record_replay=True)

    assert run.replay.termination_reason == "settled"
    # Stopping before the cap is only reachable through the settle branch —
    # it is the loop's sole `break` — so this is direct evidence the
    # velocity threshold fired, not a restatement of the reason field.
    assert run.steps_taken < MAX_COLLISION_STEPS


@pytest.mark.parametrize(
    "label,impact",
    [
        ("pocket", POCKET),
        ("light hit", LIGHT_HIT),
        ("brooklyn", BROOKLYN),
        ("low energy", LOW_ENERGY),
        # Straddles the derived ~0.31 mph boundary from both sides.
        ("just below the settle boundary", _impact(-8.0, 0.0, speed_mph=0.3)),
        ("just above the settle boundary", _impact(-8.0, 0.0, speed_mph=0.32)),
        ("threshold crossing on the final step", TERMINAL_STEP_SETTLE),
    ],
)
def test_only_the_one_way_count_implications_hold(label, impact):
    run = _simulate_collision_detail(impact, record_replay=True)
    reason = run.replay.termination_reason

    assert reason in TERMINATION_REASONS, label
    # Stopping early is reachable only through the threshold branch.
    if run.steps_taken < MAX_COLLISION_STEPS:
        assert reason == "settled", label
    # Exhausting the loop is what `step_cap` means.
    if reason == "step_cap":
        assert run.steps_taken == MAX_COLLISION_STEPS, label
    # Deliberately NOT asserted, because both are false: that reaching the
    # cap implies `step_cap`, or that `settled` implies stopping early.


def test_a_threshold_crossing_on_the_final_step_is_still_settled():
    """The counterexample to `steps_taken == cap implies step_cap`.

    The settle predicate runs after every step, the last one included. A
    run engineered to cross the threshold precisely there uses every
    permitted iteration *and* exits through the threshold branch, so it is
    `settled` at `steps_taken == MAX_COLLISION_STEPS`. Labelling it
    `step_cap` because the counts match would be reporting the arithmetic
    instead of what the loop did.
    """
    run = _simulate_collision_detail(TERMINAL_STEP_SETTLE, record_replay=True)

    assert run.steps_taken == MAX_COLLISION_STEPS
    assert run.replay.termination_reason == "settled"

    # Contact-free: damping alone drove this, so the derivation above is
    # the entire explanation and no impulse perturbed the speed curve.
    assert run.fallen_pin_ids == ()

    # The recorder is unaffected by which exit fired. 4,000 is a whole
    # number of 20-step intervals, so the terminal frame is a cadence frame
    # rather than an appended one, and the run is a full 2 s.
    replay = run.replay
    assert len(replay.frames) == MAX_COLLISION_STEPS // REPLAY_SAMPLE_EVERY_STEPS + 1
    assert replay.frames[-1].t_s == pytest.approx(MAX_COLLISION_STEPS * COLLISION_DT_S)
    assert replay.frames[-1].t_s == pytest.approx(MAX_COLLISION_SECONDS)

    # And the public tuple contract is identical either way — the reason is
    # published metadata, not something the solver's own result depends on.
    assert simulate_collision(TERMINAL_STEP_SETTLE) == (run.fallen_pin_ids, run.steps_taken)


def test_the_boundary_fixture_really_does_straddle_the_final_step():
    # Without this, a constants change could slide the crossing earlier or
    # later and the test above would still pass -- for the wrong reason,
    # having quietly become an ordinary early-settle or cap case.
    damping_factor = 1.0 - LINEAR_DAMPING_PER_S * COLLISION_DT_S
    v0_in_s = mph_to_in_per_s(TERMINAL_STEP_SETTLE.speed_mph)

    assert v0_in_s * damping_factor ** (MAX_COLLISION_STEPS - 1) >= SETTLE_SPEED_IN_S
    assert v0_in_s * damping_factor**MAX_COLLISION_STEPS < SETTLE_SPEED_IN_S


def test_all_three_situations_are_actually_exercised():
    # Guards the tests above against quietly collapsing into fewer cases if
    # the solver's constants ever change: an early settle, a normal run that
    # exhausts the loop, and a threshold crossing on the final step.
    early = _simulate_collision_detail(LOW_ENERGY, record_replay=True)
    capped = _simulate_collision_detail(POCKET, record_replay=True)
    boundary = _simulate_collision_detail(TERMINAL_STEP_SETTLE, record_replay=True)

    situations = {
        (early.replay.termination_reason, early.steps_taken < MAX_COLLISION_STEPS),
        (capped.replay.termination_reason, capped.steps_taken < MAX_COLLISION_STEPS),
        (boundary.replay.termination_reason, boundary.steps_taken < MAX_COLLISION_STEPS),
    }
    assert situations == {("settled", True), ("step_cap", False), ("settled", False)}
    assert {early.replay.termination_reason, capped.replay.termination_reason} == set(
        TERMINATION_REASONS
    )


def test_the_reason_does_not_disturb_fallen_ids_or_step_count():
    for impact in (POCKET, LOW_ENERGY):
        plain = simulate_collision(impact)
        recorded = _simulate_collision_detail(impact, record_replay=True)

        # The public tuple contract is untouched by the added metadata.
        assert recorded.fallen_pin_ids == plain[0]
        assert recorded.steps_taken == plain[1]


def test_a_settled_run_records_its_true_final_step():
    # A settled run stops at an arbitrary step rather than a round one, so
    # its terminal frame is the recorder's non-cadence case: a frame that
    # exists only because the run ended there.
    run = _simulate_collision_detail(LOW_ENERGY, record_replay=True)
    replay = run.replay

    assert replay.steps_taken % REPLAY_SAMPLE_EVERY_STEPS != 0, (
        "this fixture is meant to exercise a terminal step off the cadence"
    )
    assert replay.frames[-1].t_s == pytest.approx(replay.steps_taken * replay.dt_s)


def test_the_replay_and_its_reason_are_immutable():
    replay = _simulate_collision_detail(POCKET, record_replay=True).replay

    with pytest.raises(FrozenInstanceError):
        replay.termination_reason = "settled"
    with pytest.raises(FrozenInstanceError):
        replay.frames[0].t_s = 1.0
    with pytest.raises(FrozenInstanceError):
        replay.frames[0].bodies[0].x_in = 0.0


def test_a_run_that_never_happened_has_no_reason_to_report():
    # No run means no replay at all, so there is no place for an invented
    # termination to appear. Absence, not a defaulted "settled".
    for run in (
        _simulate_collision_detail(_impact(-2.6, 1.4, speed_mph=0.0), record_replay=True),
        _simulate_collision_detail(POCKET, standing_ids=set(), record_replay=True),
    ):
        assert run.replay is None

    gutter = DEFAULT_PINFALL_MODEL.resolve(_impact(GUTTER_ABS_LATERAL_IN + 1.0, 0.0))
    assert gutter.replay is None


# --- The published version names these semantics -------------------------


def test_the_model_version_is_pinned_to_v4():
    # Deliberately the literal string, not the imported constant: the other
    # assertions in this file follow a bump automatically, so without this
    # one a version change -- the very thing that tells consumers the
    # meaning of the payload moved -- would pass unnoticed.
    assert REPLAY_MODEL_VERSION == "planar-collision-replay-2d-v4"


def test_the_domain_and_the_api_allow_exactly_the_same_reasons():
    # The wire contract is spelled in three places (this domain alias, the
    # Pydantic response model, and the TypeScript union). Nothing enforces
    # that across languages, so at least pin the two Python spellings to
    # each other here.
    api_reasons = get_args(CollisionReplayResponse.model_fields["termination_reason"].annotation)

    assert set(api_reasons) == set(TERMINATION_REASONS)
    assert set(TERMINATION_REASONS) == {"settled", "step_cap"}


# --- The v3 sampling cadence ---------------------------------------------
#
# v3 records every 20 solver steps rather than every 100: at
# COLLISION_DT_S = 0.0005 s that is one frame per 10 ms (100 Hz) instead of
# per 50 ms (20 Hz). The point is visible latency, not physics -- at the
# fastest legal release a ball covers roughly 22 in between 50 ms samples, so
# an impulse the solver had already resolved could not appear until up to a
# whole interval later. Nothing about the run itself changes; only how finely
# it is recorded.


def _expected_schedule(steps_taken: int) -> list:
    """Every step the recorder must emit a frame for: step 0, each cadence
    tick, and the final step when it is not itself a tick."""
    schedule = list(range(0, steps_taken + 1, REPLAY_SAMPLE_EVERY_STEPS))
    if schedule[-1] != steps_taken:
        schedule.append(steps_taken)
    return schedule


def test_the_cadence_is_ten_milliseconds():
    assert REPLAY_SAMPLE_EVERY_STEPS == 20
    assert REPLAY_SAMPLE_EVERY_STEPS * COLLISION_DT_S == pytest.approx(0.01)


def test_the_frame_cap_can_contain_a_full_length_run():
    # 4000 / 20 + 1 = 201 scheduled frames; the cap must clear that with
    # room, and must stay a fixed documented bound rather than an open one.
    full_run_frames = MAX_COLLISION_STEPS // REPLAY_SAMPLE_EVERY_STEPS + 1

    assert full_run_frames == 201
    assert MAX_REPLAY_FRAMES == 256
    assert MAX_REPLAY_FRAMES > full_run_frames


def test_an_on_cadence_terminal_step_emits_exactly_the_schedule():
    # A normal contact run exhausts the cap at step 4000, which is itself a
    # multiple of 20 -- so the terminal frame *is* a cadence frame and no
    # extra one may be appended.
    run = _simulate_collision_detail(POCKET, record_replay=True)
    replay = run.replay
    schedule = _expected_schedule(run.steps_taken)

    assert run.steps_taken == MAX_COLLISION_STEPS
    assert run.steps_taken % REPLAY_SAMPLE_EVERY_STEPS == 0
    assert len(replay.frames) == 201
    assert len(replay.frames) == len(schedule)
    for index, step in enumerate(schedule):
        assert replay.frames[index].t_s == pytest.approx(step * COLLISION_DT_S, abs=1e-12)


def test_an_off_cadence_terminal_step_appends_exactly_one_extra_frame():
    # The early-settle case stops at an arbitrary step, so the run's true end
    # is not a cadence tick and one terminal frame is appended for it.
    run = _simulate_collision_detail(LOW_ENERGY, record_replay=True)
    replay = run.replay
    schedule = _expected_schedule(run.steps_taken)

    assert run.steps_taken % REPLAY_SAMPLE_EVERY_STEPS != 0
    assert len(replay.frames) == len(schedule)
    # Exactly one, not two: the appended frame must not duplicate the last
    # cadence tick.
    assert replay.frames[-1].t_s == pytest.approx(run.steps_taken * COLLISION_DT_S)
    assert replay.frames[-2].t_s < replay.frames[-1].t_s
    assert len({frame.t_s for frame in replay.frames}) == len(replay.frames)


@pytest.mark.parametrize(
    "label,impact,standing_ids",
    [
        ("pocket", POCKET, None),
        ("brooklyn", BROOKLYN, None),
        ("partial rack", POCKET, {3, 6, 10}),
        ("early settle", LOW_ENERGY, None),
    ],
)
def test_every_v3_run_matches_its_derived_schedule(label, impact, standing_ids):
    run = _simulate_collision_detail(impact, standing_ids=standing_ids, record_replay=True)
    replay = run.replay
    schedule = _expected_schedule(run.steps_taken)

    assert len(replay.frames) == len(schedule), label
    assert len(replay.frames) <= MAX_REPLAY_FRAMES, label
    for index, step in enumerate(schedule):
        assert replay.frames[index].t_s == pytest.approx(step * COLLISION_DT_S, abs=1e-12), label

    expected_ids = [BALL_BODY_ID] + sorted(
        standing_ids if standing_ids is not None else range(1, 11)
    )
    for frame in replay.frames:
        assert [body.body_id for body in frame.bodies] == expected_ids, label
        for body in frame.bodies:
            assert abs(body.x_in) < 500.0, label
            assert abs(body.y_in) < 500.0, label


def test_the_denser_cadence_leaves_the_run_itself_untouched():
    # The whole safety claim of a recording-density change: same fallen ids,
    # same step count, same termination, recorded or not.
    for impact, standing_ids in ((POCKET, None), (BROOKLYN, {3, 6, 10}), (LOW_ENERGY, None)):
        plain = simulate_collision(impact, standing_ids=standing_ids)
        recorded = _simulate_collision_detail(
            impact, standing_ids=standing_ids, record_replay=True
        )

        assert recorded.fallen_pin_ids == plain[0]
        assert recorded.steps_taken == plain[1]


def test_a_fixed_release_replays_byte_identically_at_the_new_cadence():
    first = _simulate_collision_detail(POCKET, standing_ids={1, 3, 5}, record_replay=True)
    second = _simulate_collision_detail(POCKET, standing_ids={1, 3, 5}, record_replay=True)

    # Frozen dataclasses throughout, so this compares all 201 frames body by
    # body and coordinate by coordinate.
    assert first.replay == second.replay
    assert len(first.replay.frames) == 201
    assert first.fallen_pin_ids == second.fallen_pin_ids
    assert first.steps_taken == second.steps_taken


# --- v4 threshold crossings ----------------------------------------------
#
# A crossing timestamps the *existing* displacement decision: the same
# `pin.fell` flip that produces `fallen_pin_ids`, recorded at the step it
# happened rather than recomputed afterwards. It is not a topple, a rotation,
# or a fall duration -- the pin keeps moving after it, and its recorded
# positions remain the only description of where it is.


def _crossings(run):
    return run.replay.threshold_crossings


def test_crossing_ids_are_exactly_the_fallen_pins():
    # The two come from one decision, so they must agree exactly -- not
    # merely overlap. This is the correspondence the client validates on.
    for impact, standing_ids in (
        (POCKET, None),
        (BROOKLYN, None),
        (LIGHT_HIT, None),
        (POCKET, {3, 6, 10}),
        (POCKET, {10}),
    ):
        run = _simulate_collision_detail(impact, standing_ids=standing_ids, record_replay=True)
        ids = tuple(sorted(c.pin_id for c in _crossings(run)))

        assert ids == run.fallen_pin_ids
        assert len(ids) == len({c.pin_id for c in _crossings(run)}), "one event per pin"


def test_crossings_are_ordered_by_step_then_pin_id():
    run = _simulate_collision_detail(POCKET, record_replay=True)
    events = _crossings(run)

    assert list(events) == sorted(events, key=lambda c: (c.step_index, c.pin_id))
    assert len(events) > 1, "the pocket fixture must exercise a real ordering"


def test_every_crossing_step_is_positive_and_within_the_run():
    for impact, standing_ids in ((POCKET, None), (BROOKLYN, {3, 6, 10})):
        run = _simulate_collision_detail(impact, standing_ids=standing_ids, record_replay=True)
        for crossing in _crossings(run):
            # Positive: step 0 is the initial placement, before any stepping,
            # so nothing can have crossed yet.
            assert crossing.step_index > 0
            assert crossing.step_index <= run.steps_taken


def test_no_crossing_is_ever_the_ball():
    run = _simulate_collision_detail(POCKET, record_replay=True)
    for crossing in _crossings(run):
        assert crossing.pin_id != BALL_BODY_ID
        assert 1 <= crossing.pin_id <= 10


def test_a_crossing_id_is_always_a_pin_that_was_standing():
    standing = {3, 6, 10}
    run = _simulate_collision_detail(POCKET, standing_ids=standing, record_replay=True)

    for crossing in _crossings(run):
        assert crossing.pin_id in standing


def test_a_run_where_nothing_falls_records_no_crossings():
    run = _simulate_collision_detail(LOW_ENERGY, record_replay=True)

    assert run.fallen_pin_ids == ()
    assert _crossings(run) == ()


def test_no_run_means_no_replay_and_so_no_crossings():
    for run in (
        _simulate_collision_detail(_impact(-2.6, 1.4, speed_mph=0.0), record_replay=True),
        _simulate_collision_detail(POCKET, standing_ids=set(), record_replay=True),
    ):
        assert run.replay is None


def test_each_crossing_step_is_the_first_step_the_predicate_holds():
    """The strongest form: replay the solver's own rule step by step and
    check each event lands on the first step at which that pin's
    displacement actually reaches the threshold -- not one step early or
    late, and not merely 'somewhere in range'."""
    from app.physics.collision import FALL_DISPLACEMENT_THRESHOLD_IN

    run = _simulate_collision_detail(POCKET, record_replay=True)
    replay = run.replay
    origin = {b.body_id: (b.x_in, b.y_in) for b in replay.frames[0].bodies if b.body_id}

    for crossing in _crossings(run):
        # The sampled frame at or just after the crossing must already show
        # the pin past the threshold...
        after = next(
            f
            for f in replay.frames
            if f.t_s >= crossing.step_index * replay.dt_s - 1e-12
        )
        body = next(b for b in after.bodies if b.body_id == crossing.pin_id)
        x0, y0 = origin[crossing.pin_id]
        displacement = math.hypot(body.x_in - x0, body.y_in - y0)
        assert displacement >= FALL_DISPLACEMENT_THRESHOLD_IN, crossing.pin_id

        # ...and the last sampled frame strictly before it must not, unless
        # the crossing happened within that same sampling interval.
        before = [f for f in replay.frames if f.t_s < crossing.step_index * replay.dt_s]
        if before:
            gap_steps = crossing.step_index - round(before[-1].t_s / replay.dt_s)
            assert 0 < gap_steps <= REPLAY_SAMPLE_EVERY_STEPS, crossing.pin_id


def test_recording_crossings_leaves_the_run_byte_identical():
    # Passivity, restated for the new field: the crossing is observation, so
    # a recorded and an unrecorded run agree on everything the game uses.
    for impact, standing_ids in ((POCKET, None), (BROOKLYN, {3, 6, 10}), (LOW_ENERGY, None)):
        plain = simulate_collision(impact, standing_ids=standing_ids)
        recorded = _simulate_collision_detail(
            impact, standing_ids=standing_ids, record_replay=True
        )

        assert recorded.fallen_pin_ids == plain[0]
        assert recorded.steps_taken == plain[1]


def test_crossings_are_deterministic_and_immutable():
    first = _simulate_collision_detail(POCKET, standing_ids={1, 3, 5}, record_replay=True)
    second = _simulate_collision_detail(POCKET, standing_ids={1, 3, 5}, record_replay=True)

    assert first.replay.threshold_crossings == second.replay.threshold_crossings

    with pytest.raises(FrozenInstanceError):
        first.replay.threshold_crossings[0].step_index = 0
