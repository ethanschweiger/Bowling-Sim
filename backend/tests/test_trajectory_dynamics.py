"""Skid -> hook -> roll behaviour, and the right-handed board convention.

These assert the *shape* the trajectory model is supposed to produce, not
particular board numbers pulled from a run. The previous model applied
lateral force continuously for the whole lane, so every shot curved one
way from the foul line to the pins and a right-handed release drifted
toward the bowler's left — the opposite of a conventional line. The tests
here would all have failed against it.
"""

import math
from dataclasses import replace

import pytest

from app.physics.ball import BALL_CATALOG
from app.physics.lane import DRY_FRICTION, OILED_FRICTION, LaneCondition
from app.physics.simulate import (
    FLARE_REFERENCE_DIFFERENTIAL,
    FLARE_SIDE_FRACTION,
    PATH_SAMPLE_FT,
    SLIP_EFFICIENCY,
    simulate_throw,
    step_cap_for,
)
from app.physics.throw import RELEASE_BOUNDS, Throw, sample_release
from app.physics.units import fps_to_mph, ft_to_boards, mph_to_fps, rpm_to_rad_per_s
from tests.trajectory_fixture import (
    DIAGNOSTIC_BALL_ID,
    DIAGNOSTIC_REQUEST,
    DIAGNOSTIC_SEED,
    POCKET_BOARD_RANGE,
    trace_diagnostic_throw,
)

LANE_CENTER_BOARD = 20.0


def _paired(*iterables):
    """Iterate several iterables in lockstep, stopping at the shortest --
    the explicit, 3.9-safe equivalent of `zip(..., strict=False)`. Python's
    own `strict` keyword to `zip()` needs 3.10+, one minor version above
    this project's runtime floor, and there's no annotation to defer here
    (it's a real call, not a type) so `from __future__ import annotations`
    doesn't help. Deliberately doesn't call `zip()` at all -- a wrapper
    that just forwarded to a bare `zip()` would still be exactly the
    implicit-length call Ruff's B905 flags, one layer down."""
    iterators = [iter(iterable) for iterable in iterables]
    while True:
        values = []
        for iterator in iterators:
            try:
                values.append(next(iterator))
            except StopIteration:
                return
        yield tuple(values)


# --- The right-handed pocket line ---------------------------------------


def test_the_right_handed_fixture_shows_the_full_sign_sequence():
    """Out to the right, break, then back to the left into the pocket.

    This is the directional invariant, not a carry guarantee: it asserts
    where the ball goes and which way it is moving when it arrives, and
    says nothing about how many pins that knocks down.
    """
    trace = trace_diagnostic_throw()
    terminal = trace.result.terminal

    # 1. Laid down left of centre and aimed right.
    assert trace.sampled.launch_position > LANE_CENTER_BOARD
    assert trace.sampled.launch_angle < 0

    # 2. Actually travels toward lower boards through the heads.
    assert trace.slope_between(0.0, 20.0) < 0

    # 3. Turns around *before* the pins, not at them.
    assert trace.breakpoint.distance_ft < trace.result.terminal.distance_ft
    assert trace.breakpoint.board < trace.sampled.launch_position

    # 4. Comes back toward higher boards afterwards.
    assert trace.slope_between(trace.breakpoint.distance_ft, 60.0) > 0

    # 5. Arrives in the pocket *while still moving left* — not merely
    #    passing through a pocket board on its way further right.
    low, high = POCKET_BOARD_RANGE
    assert low <= terminal.board <= high
    assert terminal.heading_deg > 0


def test_arriving_at_a_pocket_board_while_still_moving_right_would_not_count():
    # Guards the assertion above against being weakened to an endpoint
    # check: a release that reaches a similar board still heading right
    # must be distinguishable.
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["house_ball"]  # plastic: no meaningful hook
    result = simulate_throw(ball, Throw(launch_position=28.0, launch_angle=-1.0), lane)
    # Plastic ends near the same part of the lane...
    assert 14.0 <= result.terminal.board <= 20.0
    # ...but is still travelling right, so it fails the heading half.
    assert result.terminal.heading_deg < 0


# --- Phase structure ----------------------------------------------------


def test_the_heads_are_essentially_straight_and_the_back_end_is_not():
    trace = trace_diagnostic_throw()

    skid_linearity = trace.max_deviation_from_straight(0.0, 20.0)
    hook_linearity = trace.max_deviation_from_straight(30.0, 60.0)

    # Through the oiled heads the path barely departs from a straight line.
    assert skid_linearity < 0.10, (
        f"heads should skid straight, deviated {skid_linearity:.3f} boards"
    )
    # Past the pattern it clearly does.
    assert hook_linearity > skid_linearity * 3


def test_late_lane_direction_change_is_materially_greater_than_in_the_heads():
    trace = trace_diagnostic_throw()
    heads = trace.slope_between(0.0, 20.0)
    back = trace.slope_between(45.0, 60.0)

    # Not merely "some" change — a reversal.
    assert heads < 0 < back
    assert abs(back - heads) > 0.15, "the hook should be a clear change of direction"


def test_a_reactive_ball_turns_far_more_than_plastic_on_the_same_release():
    lane = LaneCondition.house_shot()
    sampled, _ = sample_release(DIAGNOSTIC_REQUEST, DIAGNOSTIC_SEED)

    def backend_turn(ball_id):
        result = simulate_throw(BALL_CATALOG[ball_id], sampled, lane)
        path = result.path
        early = min(path, key=lambda p: abs(p.distance_ft - 20.0))
        mid = min(path, key=lambda p: abs(p.distance_ft - 45.0))
        last = path[-1]
        heads_slope = (mid.board - early.board) / (mid.distance_ft - early.distance_ft)
        back_slope = (last.board - mid.board) / (last.distance_ft - mid.distance_ft)
        return abs(back_slope - heads_slope)

    reactive = backend_turn("reactive_pearl")
    plastic = backend_turn("house_ball")
    assert reactive > plastic * 5, f"reactive {reactive:.4f} vs plastic {plastic:.4f}"


def test_house_ball_stays_near_its_requested_launch_angle_line():
    """A plastic spare ball may follow its aim, but must not mimic backend hook.

    Comparing endpoints alone would confuse intended launch direction with
    hook. This calculates the endpoint from the sampled initial heading and
    holds the residual lateral read to less than one-third of a board.
    """
    lane = LaneCondition.house_shot()
    requested = Throw()

    for seed in (1, 17, 99):
        sampled, _ = sample_release(requested, seed)
        result = simulate_throw(BALL_CATALOG["house_ball"], sampled, lane)
        expected_board = sampled.launch_position + ft_to_boards(
            lane.length_ft * math.tan(math.radians(sampled.launch_angle))
        )
        assert abs(result.terminal.board - expected_board) < 1 / 3


def test_the_hook_is_self_limiting_rather_than_accelerating_forever():
    """The old model's defining flaw: lateral speed only ever grew.

    Here friction spends the slip that drives the turn, so lateral
    acceleration must fade. The last stretch of lane should therefore bend
    less than the stretch where the ball was actually turning over.
    """
    trace = trace_diagnostic_throw()
    turning = trace.max_deviation_from_straight(35.0, 52.0)
    rolling = trace.max_deviation_from_straight(52.0, 60.0)
    assert rolling < turning


# --- Inputs still matter ------------------------------------------------


LAUNCH_ANGLE = -1.5


def _run(ball_id, rotation, lane, **overrides):
    return simulate_throw(
        BALL_CATALOG[ball_id],
        Throw(launch_position=28.0, launch_angle=LAUNCH_ANGLE, axis_rotation=rotation, **overrides),
        lane,
    )


def _hook_developed(result):
    """How far the heading turned back toward higher boards over the run."""
    return result.terminal.heading_deg - LAUNCH_ANGLE


def _initial_lateral_slip(ball, throw):
    """Match the documented release reservoir for a conservation check."""
    rotation_side = math.sin(math.radians(throw.axis_rotation))
    flare_side = FLARE_SIDE_FRACTION * min(1.0, ball.differential / FLARE_REFERENCE_DIFFERENTIAL)
    side_fraction = rotation_side + (1.0 - rotation_side) * flare_side
    return (
        rpm_to_rad_per_s(throw.rev_rate)
        * (ball.radius_in / 12.0)
        * side_fraction
        * SLIP_EFFICIENCY
        * ball.hook_potential
    )


def _late_slope(result):
    breakpoint = min(result.path, key=lambda p: p.board)
    assert breakpoint.distance_ft < result.terminal.distance_ft
    last = result.path[-1]
    return (last.board - breakpoint.board) / (last.distance_ft - breakpoint.distance_ft)


def test_axis_rotation_sets_how_much_hook_is_available():
    lane = LaneCondition.house_shot()
    # More rotation puts more slip in the reservoir, so the ball finishes
    # further left.
    assert _run("reactive_pearl", 0.0, lane).terminal.board < _run(
        "reactive_pearl", 30.0, lane
    ).terminal.board < _run("reactive_pearl", 60.0, lane).terminal.board


def test_zero_axis_rotation_still_reads_the_lane():
    """Track flare, not an on/off switch.

    An earlier version scaled the slip reservoir by `sin(axis_rotation)`
    alone, so a 0-degree release had exactly zero slip and a reactive ball
    could not hook at all — the model then described that impossibility as
    physically correct. A real ball's RG differential migrates its axis as
    it travels, keeping a small side component present regardless of how it
    left the hand, so a nominally end-over-end reactive ball still turns.
    """
    lane = LaneCondition.house_shot()
    reactive = _run("reactive_pearl", 0.0, lane)
    plastic = _run("house_ball", 0.0, lane)

    # Measurable, not merely non-zero in the last decimal.
    assert _hook_developed(reactive) > 0.25, _hook_developed(reactive)
    # And still clearly separated from a low-differential plastic ball on
    # the identical release — flare scales with differential, so this is not
    # a blanket floor applied to every ball.
    assert _hook_developed(reactive) > _hook_developed(plastic) + 0.25
    assert reactive.terminal.board > plastic.terminal.board + 1.0

    # Bounded and finite, not a runaway.
    assert reactive.terminal.reached_pin_deck
    assert 0.0 <= reactive.terminal.board <= 40.0
    assert abs(reactive.terminal.heading_deg) < 45.0


def test_axis_rotation_is_a_continuum_across_its_whole_range():
    """Every rotation must matter, including above 45 degrees.

    Guards a subtle failure found in review: with lateral acceleration
    saturating far below typical slip levels, `tanh` sat at 1.0 for every
    release, acceleration ignored slip magnitude, and 45/70/90 degrees
    produced byte-identical trajectories.
    """
    lane = LaneCondition.house_shot()
    rotations = [0.0, 20.0, 45.0, 70.0, 90.0]
    runs = [_run("reactive_pearl", r, lane) for r in rotations]
    entries = [result.terminal.board for result in runs]
    headings = [result.terminal.heading_deg for result in runs]

    assert entries == sorted(entries), entries
    assert headings == sorted(headings), headings
    for a, b in _paired(headings, headings[1:]):
        assert b - a > 0.05, f"rotations too close to distinguish: {headings}"


def test_a_low_rotation_reactive_release_still_finds_the_lane():
    """Low rotation is an earlier, gentler shape — not an absence of shape."""
    lane = LaneCondition.house_shot()
    reactive = _run("reactive_pearl", 15.0, lane, rev_rate=500.0)
    plastic = _run("house_ball", 15.0, lane, rev_rate=500.0)

    path = reactive.path
    breakpoint_point = min(path, key=lambda p: p.board)

    # Out toward lower boards first.
    early = min(path, key=lambda p: abs(p.distance_ft - 20.0))
    assert early.board < path[0].board
    # Turns over before the deck, not at it.
    assert breakpoint_point.distance_ft < reactive.terminal.distance_ft
    # And comes back afterwards, arriving with a positive heading.
    after = [p for p in path if p.distance_ft > breakpoint_point.distance_ft]
    late_slope = (after[-1].board - breakpoint_point.board) / (
        after[-1].distance_ft - breakpoint_point.distance_ft
    )
    assert late_slope > 0
    assert reactive.terminal.heading_deg > 0

    # Materially different from the same release with a plastic ball.
    assert reactive.terminal.board > plastic.terminal.board + 3.0


def test_higher_axis_rotation_delays_the_breakpoint_and_sharpens_the_backend():
    """Rotation changes timing and shape, rather than only total hook."""
    lane = LaneCondition.house_shot()
    low = _run("reactive_pearl", 15.0, lane, rev_rate=500.0, axis_tilt=12.0)
    high = _run("reactive_pearl", 60.0, lane, rev_rate=500.0, axis_tilt=12.0)
    low_breakpoint = min(low.path, key=lambda p: p.board)
    high_breakpoint = min(high.path, key=lambda p: p.board)

    # Low rotation reads earlier and arcs smoothly. Higher rotation saves its
    # larger reservoir for a later, sharper move through the backend.
    assert high_breakpoint.distance_ft >= low_breakpoint.distance_ft + 0.5
    assert high.terminal.heading_deg > low.terminal.heading_deg + 0.5
    assert _late_slope(high) > _late_slope(low) * 2.0


def test_lateral_transfer_never_exceeds_the_remaining_slip_reservoir():
    """A coarse dry integration step still has to obey the same bound."""
    fresh = LaneCondition.house_shot()
    dry = replace(
        fresh,
        oil_grid=tuple(tuple(0.0 for _ in row) for row in fresh.oil_grid),
    )
    ball = BALL_CATALOG["reactive_pearl"]
    throw = Throw(
        speed_mph=12.0,
        rev_rate=150.0,
        axis_rotation=0.0,
        axis_tilt=0.0,
        launch_position=20.0,
        launch_angle=0.0,
    )

    result = simulate_throw(ball, throw, dry, step_ft=10.0)
    gained_lateral_velocity = math.tan(math.radians(result.terminal.heading_deg)) * mph_to_fps(
        result.terminal.speed_mph
    )

    assert gained_lateral_velocity <= _initial_lateral_slip(ball, throw) + 1e-9


def test_axis_tilt_delays_the_hook_without_forbidding_it():
    """Tilt is a timing knob, not a cap.

    The failure mode this guards against is modelling tilt as a permanent
    force multiplier, where a very tilted release simply never hooks. Here
    every tilt still develops real hook — the ball just takes longer to
    spend the same slip, so its breakpoint moves down the lane and its
    backend is more gradual.
    """
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["reactive_pearl"]
    launch_angle = -1.5

    def run(tilt):
        return simulate_throw(
            ball, Throw(launch_position=28.0, launch_angle=launch_angle, axis_tilt=tilt), lane
        )

    tilts = [0.0, 30.0, 60.0, RELEASE_BOUNDS["axis_tilt"][1]]
    runs = [run(t) for t in tilts]

    for tilt, result in _paired(tilts, runs):
        # No tilt value suppresses hook entirely: the heading always turns
        # substantially back toward higher boards from where it launched.
        developed = result.terminal.heading_deg - launch_angle
        assert developed > 1.0, f"tilt {tilt} developed only {developed:.3f} deg of hook"

    breakpoints = [min(r.path, key=lambda p: p.board).distance_ft for r in runs]
    # More tilt holds the line further down the lane.
    assert breakpoints == sorted(breakpoints), breakpoints
    assert breakpoints[-1] > breakpoints[0]

    # And, having turned later, it finishes further right within the lane.
    assert runs[-1].terminal.board < runs[0].terminal.board


def test_rev_rate_and_speed_visibly_change_the_result():
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["reactive_pearl"]
    base = Throw(launch_position=28.0, launch_angle=-1.5)

    slow_revs = simulate_throw(ball, Throw(**{**base.__dict__, "rev_rate": 150.0}), lane)
    fast_revs = simulate_throw(ball, Throw(**{**base.__dict__, "rev_rate": 500.0}), lane)
    assert fast_revs.terminal.board > slow_revs.terminal.board

    slow_ball = simulate_throw(ball, Throw(**{**base.__dict__, "speed_mph": 12.0}), lane)
    fast_ball = simulate_throw(ball, Throw(**{**base.__dict__, "speed_mph": 22.0}), lane)
    # More time on the lane means more chance to convert slip.
    assert slow_ball.terminal.board != fast_ball.terminal.board


def test_more_oil_means_less_hook():
    ball = BALL_CATALOG["reactive_pearl"]
    throw = Throw(launch_position=28.0, launch_angle=-1.5)

    fresh = LaneCondition.house_shot()
    # A drier lane: same pattern shape, no oil at all.
    stripped = LaneCondition(
        spec=fresh.spec,
        oil_grid=tuple(tuple(0.0 for _ in row) for row in fresh.oil_grid),
        peak_oil_ml=fresh.peak_oil_ml,
        temperature_f=fresh.temperature_f,
    )

    oiled_entry = simulate_throw(ball, throw, fresh).terminal.board
    dry_entry = simulate_throw(ball, throw, stripped).terminal.board
    assert dry_entry > oiled_entry, "a dry lane should hook the ball further left"


# --- Each remaining control makes its own explained difference ----------
#
# Codex's review flagged that no test directly proved launch_position or
# launch_angle affect the route, and that the speed test only checked "the
# board changes" without checking the reported speed is expressed in real,
# consistent units. These three close that gap. Rotation is held at 0 deg
# in the position/angle checks specifically so the (small, differential-
# scaled) flare residual has negligible time to act before the sample
# point — isolating what launch_position/launch_angle alone contribute,
# not a mix of release geometry and hook.


def test_launch_position_shifts_the_whole_route_laterally():
    """The laydown board is a lateral offset applied to the whole path, not
    a value that only affects the reported endpoint."""
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["reactive_pearl"]
    board_gap = 5.0

    near = simulate_throw(
        ball, Throw(launch_position=25.0, launch_angle=-1.5, axis_rotation=0.0), lane
    )
    far = simulate_throw(
        ball, Throw(launch_position=25.0 + board_gap, launch_angle=-1.5, axis_rotation=0.0), lane
    )

    # Every recorded sample is offset by very nearly the same amount — this
    # is a translation of the route, not a change to its shape.
    for a, b in _paired(near.path, far.path):
        assert a.distance_ft == b.distance_ft
        assert abs((b.board - a.board) - board_gap) < 0.75, (a, b)

    # The offset survives all the way to the headpin plane, not just the
    # release point.
    assert far.terminal.board > near.terminal.board
    assert abs((far.terminal.board - near.terminal.board) - board_gap) < 1.0


def test_launch_angle_changes_initial_direction():
    """Launch angle sets the release's initial heading — sign and rough
    magnitude — before any hook has had distance to develop."""
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["reactive_pearl"]

    def early_slope(angle):
        result = simulate_throw(
            ball, Throw(launch_position=20.0, launch_angle=angle, axis_rotation=0.0), lane
        )
        sample = min(result.path, key=lambda p: abs(p.distance_ft - 2.0))
        return (sample.board - result.path[0].board) / sample.distance_ft

    negative, zero, positive = early_slope(-2.0), early_slope(0.0), early_slope(2.0)

    # Sign follows the requested angle: aimed right moves right first,
    # aimed left moves left first, aimed straight starts straight.
    assert negative < 0 < positive
    assert zero == pytest.approx(0.0, abs=1e-9)

    # Magnitude tracks the release's own tangent, in the model's declared
    # board-width units — not merely "some" initial drift.
    expected = ft_to_boards(math.tan(math.radians(2.0)))
    assert positive == pytest.approx(expected, rel=0.05)
    assert negative == pytest.approx(-expected, rel=0.05)


def test_speed_changes_terminal_state_with_units_preserved():
    """Speed changes the reported entry speed in real, consistent mph — not
    a value silently expressed in the wrong unit domain."""
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["reactive_pearl"]
    requested_speeds = (12.0, 17.0, 22.0)

    terminals = []
    for speed in requested_speeds:
        result = simulate_throw(
            ball, Throw(launch_position=28.0, launch_angle=-1.5, speed_mph=speed), lane
        )
        terminal_speed = result.terminal.speed_mph

        # Friction only ever removes forward speed in this model, and never
        # by an implausible amount over one 60 ft lane.
        assert 0.0 < terminal_speed < speed
        assert speed - terminal_speed < 3.0, (
            "one lane's friction should not bleed off more than a few mph"
        )

        # Round-tripping through the same conversion the simulator itself
        # uses must reproduce the value exactly — proof this is genuinely
        # mph, not an fps figure the API happens to relabel.
        assert fps_to_mph(mph_to_fps(terminal_speed)) == pytest.approx(terminal_speed, abs=1e-9)
        terminals.append(terminal_speed)

    # And the ordering survives: releasing faster arrives faster.
    assert terminals == sorted(terminals)
    assert terminals[0] < terminals[-1]


def test_friction_stays_within_its_declared_bounds_everywhere():
    lane = LaneCondition.house_shot()
    for distance in range(0, 61, 3):
        for board in range(1, 40, 2):
            assert OILED_FRICTION <= lane.friction_at(float(distance), float(board)) <= DRY_FRICTION


# --- Determinism and bounded variation ----------------------------------


def test_the_same_seed_reproduces_the_trajectory_exactly():
    first = trace_diagnostic_throw()
    second = trace_diagnostic_throw()
    assert first.sampled == second.sampled
    assert first.result.terminal == second.result.terminal
    assert [p.board for p in first.result.path] == [p.board for p in second.result.path]


def test_alternate_seeds_vary_but_never_leave_legal_state():
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG[DIAGNOSTIC_BALL_ID]
    boards = []
    for seed in range(1, 60):
        sampled, _ = sample_release(DIAGNOSTIC_REQUEST, seed)
        result = simulate_throw(ball, sampled, lane)
        boards.append(result.terminal.board)
        assert result.terminal.reached_pin_deck
        assert 0.0 <= result.terminal.board <= 40.0
        assert result.terminal.speed_mph >= 0.0
    assert len(set(boards)) > 1, "seeded release variance should actually vary the outcome"


# --- Numerical quality --------------------------------------------------


@pytest.mark.parametrize("fine_step", [0.025, 0.01])
def test_refining_the_integration_step_does_not_move_the_answer(fine_step):
    """Halving (and quartering) the stride must converge, not wander."""
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG[DIAGNOSTIC_BALL_ID]
    sampled, _ = sample_release(DIAGNOSTIC_REQUEST, DIAGNOSTIC_SEED)

    coarse = simulate_throw(ball, sampled, lane)
    fine = simulate_throw(ball, sampled, lane, step_ft=fine_step)

    assert abs(fine.terminal.board - coarse.terminal.board) < 0.25, (
        "endpoint should be step-independent"
    )
    assert abs(fine.terminal.heading_deg - coarse.terminal.heading_deg) < 0.25
    # And the qualitative shape is unchanged.
    assert fine.terminal.heading_deg > 0
    assert min(p.board for p in fine.path) < fine.terminal.board


def test_the_path_has_no_single_sample_kink():
    """A step change in friction at a grid boundary used to be able to put
    a corner in the path. With the field interpolated, consecutive slope
    changes should stay in the same order of magnitude."""
    trace = trace_diagnostic_throw()
    path = trace.result.path
    slopes = [
        (path[i + 1].board - path[i].board) / (path[i + 1].distance_ft - path[i].distance_ft)
        for i in range(len(path) - 1)
    ]
    changes = [abs(slopes[i + 1] - slopes[i]) for i in range(len(slopes) - 1)]
    largest = max(changes)
    median = sorted(changes)[len(changes) // 2]
    assert largest < median * 12 + 1e-6, (
        f"one sample turned much harder than its neighbours ({largest:.4f})"
    )


def test_returned_path_stays_bounded_even_at_a_fine_integration_step():
    """Path sampling is decoupled from integration precision, so refining
    the model must not inflate the response."""
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG[DIAGNOSTIC_BALL_ID]
    bound = int(lane.length_ft / PATH_SAMPLE_FT) + 2

    for step in (0.05, 0.01):
        result = simulate_throw(ball, Throw(launch_angle=-1.5), lane, step_ft=step)
        assert len(result.path) <= bound, (step, len(result.path))
        # Far fewer samples than integration steps, which is the point.
        assert len(result.path) < step_cap_for(lane.length_ft, step)


def test_gutter_protection_still_holds():
    lane = LaneCondition.house_shot()
    ball = BALL_CATALOG["reactive_pearl"]
    for angle in (RELEASE_BOUNDS["launch_angle"][0], RELEASE_BOUNDS["launch_angle"][1]):
        for board in (1.0, 39.0):
            result = simulate_throw(ball, Throw(launch_position=board, launch_angle=angle), lane)
            assert 0.0 <= result.terminal.board <= 40.0
            for point in result.path:
                assert 0.0 <= point.board <= 40.0
