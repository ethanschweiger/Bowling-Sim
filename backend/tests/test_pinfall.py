"""Pinfall resolution: the heuristic is deterministic — same ImpactState in,
same PinfallResult out, no random source involved.
"""

import random

from app.physics.impact import ImpactState
from app.physics.pinfall import EntryAngleHeuristicPinfallModel

DEFAULT_PINFALL_MODEL = EntryAngleHeuristicPinfallModel()

SAMPLE_IMPACT = ImpactState(
    lateral_position_in=-2.0,
    heading_deg=4.5,
    speed_mph=16.3,
    ball_mass_lbs=15.0,
    ball_radius_in=4.29,
    lane_condition_version=3,
)


def test_heuristic_is_deterministic_for_identical_impact_input():
    results = [DEFAULT_PINFALL_MODEL.resolve(SAMPLE_IMPACT) for _ in range(20)]
    assert len(set(r.pins_knocked for r in results)) == 1
    assert all(r.model_id == DEFAULT_PINFALL_MODEL.model_id for r in results)


def test_heuristic_result_unaffected_by_global_random_state():
    random.seed(1)
    a = DEFAULT_PINFALL_MODEL.resolve(SAMPLE_IMPACT)
    random.seed(999999)
    b = DEFAULT_PINFALL_MODEL.resolve(SAMPLE_IMPACT)
    assert a == b


def test_pins_knocked_stays_within_bounds_across_many_impacts():
    model = EntryAngleHeuristicPinfallModel()
    for lateral in range(-25, 26, 5):
        for heading in range(-10, 11, 5):
            impact = ImpactState(
                lateral_position_in=float(lateral),
                heading_deg=float(heading),
                speed_mph=16.0,
                ball_mass_lbs=15.0,
                ball_radius_in=4.29,
                lane_condition_version=1,
            )
            result = model.resolve(impact)
            assert 0 <= result.pins_knocked <= 10
            assert result.model_id == "entry-angle-heuristic-v1"
            assert result.limitations
