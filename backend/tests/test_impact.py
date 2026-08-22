"""Impact construction: a completed trajectory produces a finite, correctly
oriented ImpactState at the headpin plane, carrying the lane-condition
version it actually ran against.
"""

import math

from app.physics.ball import BALL_CATALOG
from app.physics.impact import impact_state_from_result
from app.physics.lane import LaneCondition
from app.physics.pin_deck import LANE_CENTER_BOARD
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw
from app.physics.units import BOARD_WIDTH_IN


def test_impact_state_is_finite_and_carries_the_lane_condition_version():
    ball = BALL_CATALOG["reactive_pearl"]
    throw = Throw(speed_mph=17.0, rev_rate=350.0, axis_rotation=45.0, axis_tilt=15.0, launch_angle=0.5, launch_position=28.0)
    lane = LaneCondition.house_shot()

    result = simulate_throw(ball, throw, lane)
    impact = impact_state_from_result(result, ball)

    assert math.isfinite(impact.lateral_position_in)
    assert math.isfinite(impact.heading_deg)
    assert math.isfinite(impact.speed_mph)
    assert impact.speed_mph >= 0.0
    assert impact.lane_condition_version == result.lane_condition_version == lane.version
    assert impact.ball_mass_lbs == ball.mass_lbs
    assert impact.ball_radius_in == ball.radius_in


def test_impact_state_lateral_position_is_correctly_oriented_from_the_terminal_state():
    ball = BALL_CATALOG["reactive_pearl"]
    # Board 28 is *left* of center: board 1 is the bowler's right gutter and
    # board 39 the bowler's left, so center is board 20 and higher numbers
    # run left. (This comment previously said "right of center", which
    # inverted the documented convention.)
    # launch_angle is held below the default 2.0 deliberately: at the
    # default this release runs all the way to the left lane edge and
    # clamps at board 40, which is both an unrepresentative endpoint and
    # an exact integer (see the final assertion).
    throw = Throw(launch_position=28.0, launch_angle=0.5)
    lane = LaneCondition.house_shot()

    result = simulate_throw(ball, throw, lane)
    impact = impact_state_from_result(result, ball)

    # A terminal board above center must read as a positive
    # lateral_position_in, with the magnitude matching the declared board
    # width — and it must be derived from the exact terminal state, not
    # from the rounded `entry_board` presentation field.
    expected_in = (result.terminal.board - LANE_CENTER_BOARD) * BOARD_WIDTH_IN
    assert impact.lateral_position_in == expected_in
    assert impact.lateral_position_in > 0
    # The earlier version of this test used a fixture that clamped at the
    # lane edge (board 40), where the board is an exact integer and every
    # rounding route agrees — so it passed without testing anything.
    assert result.terminal.board != int(result.terminal.board)
