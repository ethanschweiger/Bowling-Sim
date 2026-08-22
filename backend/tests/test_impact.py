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


def test_impact_state_lateral_position_is_correctly_oriented_from_entry_board():
    ball = BALL_CATALOG["house_ball"]
    throw = Throw(launch_position=28.0)  # right of center
    lane = LaneCondition.house_shot()

    result = simulate_throw(ball, throw, lane)
    impact = impact_state_from_result(result, ball)

    # entry_board > LANE_CENTER_BOARD (28 > 20) must read as a positive
    # lateral_position_in, and the magnitude must match the declared board width.
    expected_in = (result.entry_board - LANE_CENTER_BOARD) * BOARD_WIDTH_IN
    assert impact.lateral_position_in == expected_in
    assert impact.lateral_position_in > 0
