"""Tests specific to the Physics v1 correctness-fix milestone: unit basis,
boundary safety, seed replay, documented oil volume, and session atomicity.
"""

import math
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.physics.ball import BALL_CATALOG
from app.physics.lane import HOUSE_SHOT_SPEC, LaneCondition
from app.physics.lane_session import LaneSession
from app.physics.simulate import STEP_FT, simulate_throw
from app.physics.throw import RELEASE_BOUNDS, Throw, sample_release
from app.physics.units import fps_to_mph, mph_to_fps, rpm_to_rad_per_s


def test_known_nominal_speed_produces_the_correct_time_basis():
    # 15 mph is exactly 22 ft/s (15 * 5280/3600) — a clean number to check
    # the conversion isn't silently off by the mph<->fps factor anywhere.
    fps = mph_to_fps(15.0)
    assert fps == pytest.approx(22.0)

    dt = STEP_FT / fps
    assert dt == pytest.approx(0.5 / 22.0)
    assert fps_to_mph(fps) == pytest.approx(15.0)

    # 60 rpm is exactly one revolution per second.
    assert rpm_to_rad_per_s(60.0) == pytest.approx(2 * math.pi)


def test_boundary_valued_requests_stay_within_range_across_many_seeds():
    lo_throw = Throw(**{field: bounds[0] for field, bounds in RELEASE_BOUNDS.items()})
    hi_throw = Throw(**{field: bounds[1] for field, bounds in RELEASE_BOUNDS.items()})
    ball = BALL_CATALOG["particle_beast"]  # highest hook_potential in the catalog — stresses lateral motion hardest
    lane = LaneCondition.house_shot()

    for base_throw in (lo_throw, hi_throw):
        for seed in range(200):
            sampled, _ = sample_release(base_throw, seed=seed)
            for field, (lo, hi) in RELEASE_BOUNDS.items():
                assert lo <= getattr(sampled, field) <= hi

            result = simulate_throw(ball, sampled, lane)
            assert math.isfinite(result.entry_board)
            assert math.isfinite(result.entry_angle_deg)
            assert math.isfinite(result.speed_at_pins_mph)
            assert 0.0 <= result.entry_board <= lane.board_count + 1
            assert result.speed_at_pins_mph >= 0.0


def test_seed_replay_reproduces_release_and_trajectory():
    ball = BALL_CATALOG["reactive_pearl"]
    lane = LaneCondition.house_shot()
    requested = Throw(
        speed_mph=16.0, rev_rate=340.0, axis_rotation=50.0,
        axis_tilt=12.0, launch_angle=1.0, launch_position=25.0,
    )

    sampled_a, seed_a = sample_release(requested, seed=99)
    sampled_b, seed_b = sample_release(requested, seed=99)
    assert seed_a == seed_b
    assert sampled_a == sampled_b

    result_a = simulate_throw(ball, sampled_a, lane)
    result_b = simulate_throw(ball, sampled_b, lane)
    assert result_a.path == result_b.path
    assert result_a.entry_board == result_b.entry_board
    assert result_a.entry_angle_deg == result_b.entry_angle_deg


def test_house_shot_grid_sums_to_its_documented_volume():
    condition = LaneCondition.house_shot()
    total_ml = sum(sum(row) for row in condition.oil_grid)
    assert total_ml == pytest.approx(HOUSE_SHOT_SPEC.total_volume_ml, rel=1e-9)


def test_session_records_sequential_conditions_without_a_race():
    session = LaneSession()
    ball = BALL_CATALOG["house_ball"]
    throw = Throw()
    throw_count = 50

    def one_throw(_):
        return session.run_throw(lambda condition: simulate_throw(ball, throw, condition))

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(one_throw, range(throw_count)))

    versions = sorted(result.lane_condition_version for result in results)
    # Every version from 1..N used exactly once — no throw read a condition
    # another throw had already read, and none was lost to a split write.
    assert versions == list(range(1, throw_count + 1))
    assert session.condition.version == throw_count + 1
