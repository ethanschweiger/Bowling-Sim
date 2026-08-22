from app.physics.ball import BALL_CATALOG
from app.physics.lane import LaneCondition, apply_wear
from app.physics.simulate import TrajectoryPoint, simulate_throw
from app.physics.throw import Throw

STRAIGHT_DOWN_BOARD_20 = [TrajectoryPoint(distance_ft=float(ft), board=20.0) for ft in range(0, 33)]


def test_a_throw_changes_lane_state():
    fresh = LaneCondition.house_shot()
    worn = apply_wear(fresh, STRAIGHT_DOWN_BOARD_20)

    assert worn.version == fresh.version + 1
    assert worn.oil_grid != fresh.oil_grid

    # Oil only ever goes down or gets redistributed — never up beyond what
    # carrydown adds back — and specifically drops along the touched path.
    for point in STRAIGHT_DOWN_BOARD_20:
        before = fresh.oil_at(point.distance_ft, point.board)
        after = worn.oil_at(point.distance_ft, point.board)
        if before > 0:
            assert after < before


def test_changed_lane_state_changes_a_later_trajectory():
    ball = BALL_CATALOG["reactive_pearl"]
    throw = Throw(
        speed_mph=17.0, rev_rate=380.0, axis_rotation=55.0,
        axis_tilt=10.0, launch_angle=1.5, launch_position=20.0,
    )

    fresh = LaneCondition.house_shot()
    first_result = simulate_throw(ball, throw, fresh)

    # Wear the same path in repeatedly to meaningfully dry it out — one
    # pass is a small, bounded change, so we run several to see its effect.
    worn = fresh
    for _ in range(30):
        worn = apply_wear(worn, first_result.path)

    second_result = simulate_throw(ball, throw, worn)

    assert second_result.entry_board != first_result.entry_board


def test_house_shot_has_the_documented_friction_profile():
    from app.physics.lane import DRY_FRICTION, HOUSE_SHOT_SPEC, OILED_FRICTION

    condition = LaneCondition.house_shot()

    # Dead center of the pattern, well before the taper: fully oiled.
    assert condition.friction_at(distance_ft=10.0, board=20.0) == OILED_FRICTION

    # Outside the pattern's board range entirely: dry.
    assert condition.friction_at(distance_ft=10.0, board=1.0) == DRY_FRICTION

    # Past the pattern's stated length, even on a favored board: dry.
    assert condition.friction_at(distance_ft=40.0, board=20.0) == DRY_FRICTION
    assert HOUSE_SHOT_SPEC.length_ft == 32.0
