"""Tests specific to the coordinate/lane-parameter fix: board width as a
declared length unit, no artificial clipping under ordinary inputs, release
angle direction, and a restored, bounded temperature_f effect.
"""

import math

import pytest

from app.physics.ball import BALL_CATALOG
from app.physics.lane import DRY_FRICTION, OILED_FRICTION, LaneCondition, apply_wear
from app.physics.simulate import TrajectoryPoint, simulate_throw
from app.physics.throw import Throw
from app.physics.units import boards_to_ft, boards_to_in, ft_to_boards


def test_one_board_converts_to_1_05_inches_exactly():
    assert boards_to_in(1.0) == pytest.approx(1.05)


def test_board_and_feet_conversions_round_trip():
    assert ft_to_boards(boards_to_ft(4.0)) == pytest.approx(4.0)


REPRESENTATIVE_THROWS = [
    Throw(speed_mph=15.0, rev_rate=280.0, axis_rotation=35.0, axis_tilt=20.0, launch_angle=0.2, launch_position=25.0),
    Throw(speed_mph=17.0, rev_rate=350.0, axis_rotation=45.0, axis_tilt=15.0, launch_angle=0.5, launch_position=22.0),
    Throw(speed_mph=19.0, rev_rate=420.0, axis_rotation=55.0, axis_tilt=10.0, launch_angle=0.8, launch_position=20.0),
]


def test_representative_throws_stay_in_bounds_without_artificial_clipping():
    lane = LaneCondition.house_shot()
    for throw in REPRESENTATIVE_THROWS:
        for ball_id in BALL_CATALOG:
            result = simulate_throw(BALL_CATALOG[ball_id], throw, lane)

            assert math.isfinite(result.entry_board)
            assert math.isfinite(result.entry_angle_deg)
            assert math.isfinite(result.speed_at_pins_mph)
            assert result.speed_at_pins_mph >= 0.0

            # A believable throw shouldn't need the lane-edge safety clamp —
            # if it does, the hook term is tuned too hot for ordinary input.
            assert 0.0 < result.entry_board < lane.board_count + 1


def test_release_angle_moves_the_path_in_the_documented_lateral_direction():
    ball = BALL_CATALOG["house_ball"]
    lane = LaneCondition.house_shot()
    # Zero rotation leaves only the small documented flare residual. It stays
    # far too small to reverse the requested launch-angle direction here.
    straight = Throw(speed_mph=17.0, rev_rate=350.0, axis_rotation=0.0, axis_tilt=15.0, launch_angle=0.0, launch_position=20.0)
    angled = Throw(speed_mph=17.0, rev_rate=350.0, axis_rotation=0.0, axis_tilt=15.0, launch_angle=5.0, launch_position=20.0)

    straight_result = simulate_throw(ball, straight, lane)
    angled_result = simulate_throw(ball, angled, lane)

    assert straight_result.entry_board == pytest.approx(20.0, abs=0.05)
    # Positive launch_angle -> positive lateral velocity -> higher board number.
    assert angled_result.entry_board > straight_result.entry_board


def test_temperature_is_retained_by_fresh_and_worn_conditions():
    fresh = LaneCondition.house_shot(temperature_f=90.0)
    assert fresh.temperature_f == 90.0

    path = [TrajectoryPoint(distance_ft=float(ft), board=20.0) for ft in range(0, 33)]
    worn = apply_wear(fresh, path)
    assert worn.temperature_f == 90.0


def test_temperature_friction_effect_is_bounded_and_measurable():
    # Board 5 sits in the pattern's lateral taper zone (between total_boards
    # and center_boards), so it holds partial oil — enough headroom for the
    # temperature multiplier to move friction without hitting a hard clamp.
    cold = LaneCondition.house_shot(temperature_f=40.0)
    hot = LaneCondition.house_shot(temperature_f=100.0)

    cold_friction = cold.friction_at(distance_ft=5.0, board=5.0)
    hot_friction = hot.friction_at(distance_ft=5.0, board=5.0)

    assert OILED_FRICTION <= cold_friction <= DRY_FRICTION
    assert OILED_FRICTION <= hot_friction <= DRY_FRICTION
    assert hot_friction != cold_friction

    # Even at absurd extremes, the adjustment never pushes friction outside
    # the model's global bounds.
    extreme = LaneCondition.house_shot(temperature_f=500.0)
    assert OILED_FRICTION <= extreme.friction_at(distance_ft=5.0, board=5.0) <= DRY_FRICTION
