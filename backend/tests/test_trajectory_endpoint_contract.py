"""The canonical endpoint contract: one unrounded terminal state, and
everything downstream derived from it.

Before `TerminalState` existed, the recorded path and the API's entry
fields were rounded independently from the same loop variables at
different precisions, and `ImpactState` was built from the *rounded*
`entry_board`. The picture the bowler saw and the state the collision
model consumed could therefore differ by a rounding step. These tests
pin that contract down so it can't silently regress.
"""

import math

import pytest

from app.physics.ball import BALL_CATALOG
from app.physics.impact import TruncatedTrajectoryError, impact_state_from_result
from app.physics.lane import LaneCondition
from app.physics.pin_deck import LANE_CENTER_BOARD
from app.physics.simulate import (
    BOARD_DECIMALS,
    STEP_CAP_GUARD,
    SimulationResult,
    TerminalState,
    simulate_throw,
    step_cap_for,
)
from app.physics.throw import RELEASE_BOUNDS, Throw
from app.physics.units import BOARD_WIDTH_IN, boards_to_in

from tests.trajectory_fixture import trace_diagnostic_throw

# `entry_board` is `round(terminal.board, BOARD_DECIMALS)`, so it can sit at
# most half a unit in the last kept place away from the exact endpoint.
BOARD_ROUNDING_TOLERANCE = 0.5 * 10**-BOARD_DECIMALS


# --- One endpoint, three consumers -------------------------------------


def test_entry_board_is_exactly_the_final_recorded_path_point():
    trace = trace_diagnostic_throw()
    # Not "close to" — the same number. The Canvas draws the last path
    # point as its entry marker, so any divergence here is a marker that
    # disagrees with the polyline it sits on.
    assert trace.result.entry_board == trace.last_path_point.board


def test_impact_derives_from_the_unrounded_terminal_state_not_the_rounded_entry_field():
    trace = trace_diagnostic_throw()
    terminal = trace.result.terminal

    expected_from_terminal = boards_to_in(terminal.board - LANE_CENTER_BOARD)
    assert trace.impact.lateral_position_in == expected_from_terminal

    # And prove that is a real distinction for this fixture: deriving from
    # the rounded entry field would have given a different number. (If the
    # endpoint ever lands exactly on a rounding boundary this assertion
    # would be vacuous, so assert it isn't.)
    from_rounded = boards_to_in(trace.result.entry_board - LANE_CENTER_BOARD)
    assert trace.impact.lateral_position_in != from_rounded, (
        "fixture endpoint is exactly representable, so this test cannot distinguish "
        "the two derivation routes — pick a fixture with a fractional endpoint"
    )


def test_impact_heading_and_speed_are_the_unrounded_terminal_values():
    trace = trace_diagnostic_throw()
    terminal = trace.result.terminal
    assert trace.impact.heading_deg == terminal.heading_deg
    assert trace.impact.speed_mph == terminal.speed_mph


def test_rounded_entry_fields_stay_within_the_documented_tolerance_of_the_endpoint():
    trace = trace_diagnostic_throw()
    terminal = trace.result.terminal
    assert abs(trace.result.entry_board - terminal.board) <= BOARD_ROUNDING_TOLERANCE
    # Expressed in inches, the presentation rounding is far below any
    # physically meaningful scale (a pin is ~4.77 in across).
    assert abs(boards_to_in(trace.result.entry_board - terminal.board)) < 0.001


def test_endpoint_agreement_holds_across_balls_and_seeds():
    lane = LaneCondition.house_shot()
    for ball_id, ball in BALL_CATALOG.items():
        for seed in (1, 7, 99, 4242):
            trace = trace_diagnostic_throw(ball_id=ball_id, seed=seed)
            assert trace.result.entry_board == trace.last_path_point.board, (ball_id, seed)
            expected = boards_to_in(trace.result.terminal.board - LANE_CENTER_BOARD)
            assert trace.impact.lateral_position_in == expected, (ball_id, seed)
    assert lane.version == 1  # the sweep never mutated the shared condition


# --- Terminal validity --------------------------------------------------


def test_every_legal_release_reaches_the_headpin_plane():
    lane = LaneCondition.house_shot()
    lo_speed, hi_speed = RELEASE_BOUNDS["speed_mph"]
    lo_revs, hi_revs = RELEASE_BOUNDS["rev_rate"]

    for ball in BALL_CATALOG.values():
        for speed in (lo_speed, 17.0, hi_speed):
            for revs in (lo_revs, hi_revs):
                result = simulate_throw(ball, Throw(speed_mph=speed, rev_rate=revs), lane)
                assert result.terminal.reached_pin_deck, (ball.id, speed, revs)
                assert result.terminal.distance_ft == pytest.approx(lane.length_ft)


def test_a_truncated_trajectory_is_refused_rather_than_scored():
    # Built directly: a route that stopped at 40 ft is not an entry state,
    # however plausible its board looks.
    truncated = SimulationResult(
        path=[],
        entry_board=18.0,
        entry_angle_deg=3.0,
        speed_at_pins_mph=15.0,
        lane_condition_version=1,
        terminal=TerminalState(
            distance_ft=40.0,
            board=18.0,
            heading_deg=3.0,
            speed_mph=15.0,
            reached_pin_deck=False,
        ),
    )
    with pytest.raises(TruncatedTrajectoryError) as excinfo:
        impact_state_from_result(truncated, BALL_CATALOG["reactive_pearl"])
    assert "40.000" in str(excinfo.value)


def test_reaching_the_deck_is_measured_against_the_lane_not_a_hardcoded_60():
    # A shorter lane must still register as reached at its own length.
    lane = LaneCondition.house_shot()
    result = simulate_throw(BALL_CATALOG["house_ball"], Throw(), lane)
    assert result.terminal.distance_ft == pytest.approx(lane.length_ft)
    assert result.terminal.reached_pin_deck


# --- The step cap scales with integration precision ---------------------


def test_step_cap_is_derived_from_lane_length_and_stride():
    # The exact failure Codex flagged: a cap tuned for 0.5 ft silently
    # truncates a 0.1 ft refinement at 40 ft. A derived cap cannot.
    assert step_cap_for(60.0, 0.5) == math.ceil(60.0 / 0.5) + STEP_CAP_GUARD
    assert step_cap_for(60.0, 0.1) >= 600
    assert step_cap_for(60.0, 0.1) > step_cap_for(60.0, 0.5)


def test_step_cap_always_leaves_headroom_to_cross_the_lane():
    for step in (1.0, 0.5, 0.25, 0.1, 0.05):
        needed = math.ceil(60.0 / step)
        assert step_cap_for(60.0, step) > needed, step


def test_step_cap_rejects_a_nonpositive_stride():
    for bad in (0.0, -0.5):
        with pytest.raises(ValueError):
            step_cap_for(60.0, bad)


def test_returned_path_length_is_bounded_by_the_step_cap():
    # Visual fidelity must not quietly become an unbounded API cost: the
    # response carries at most one point per integration step, plus the
    # release point. Anything that raises path density has to raise this
    # documented bound deliberately.
    lane = LaneCondition.house_shot()
    bound = step_cap_for(lane.length_ft) + 1
    for ball in BALL_CATALOG.values():
        for angle in (-2.0, 0.0, 2.0):
            result = simulate_throw(ball, Throw(launch_angle=angle), lane)
            assert len(result.path) <= bound, (ball.id, angle, len(result.path))
    # And the bound is tight enough to be meaningful, not vacuously large.
    assert bound <= 200


# --- Determinism --------------------------------------------------------


def test_the_diagnostic_fixture_is_reproducible():
    first = trace_diagnostic_throw()
    second = trace_diagnostic_throw()

    assert first.sampled == second.sampled
    assert first.seed == second.seed
    assert first.result.terminal == second.result.terminal
    assert first.impact == second.impact
    assert first.pinfall.fallen_pin_ids == second.pinfall.fallen_pin_ids


def test_the_terminal_state_is_immutable():
    terminal = trace_diagnostic_throw().result.terminal
    with pytest.raises(Exception):
        terminal.board = 1.0  # type: ignore[misc]


def test_board_width_conversion_is_the_shared_one():
    # Guards against a future parallel conversion constant creeping in.
    assert boards_to_in(1.0) == BOARD_WIDTH_IN
