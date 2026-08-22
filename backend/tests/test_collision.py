"""Deterministic 2D pin-collision model: reproducibility, ID validity,
zero-speed/gutter safety, a centered strike-line hit, pairwise impulse
correctness (no overlap, no energy gain), and bounded termination.
"""

import math

import pytest

from app.physics.collision import (
    COLLISION_RESTITUTION,
    MAX_COLLISION_STEPS,
    PlanarCollisionPinfallModel,
    _Body,
    _resolve_pair,
    simulate_collision,
)
from app.physics.impact import ImpactState
from app.physics.pin_deck import GUTTER_ABS_LATERAL_IN

MODEL = PlanarCollisionPinfallModel()


def _impact(**overrides) -> ImpactState:
    base = dict(
        lateral_position_in=0.0,
        heading_deg=0.0,
        speed_mph=16.0,
        ball_mass_lbs=15.0,
        ball_radius_in=4.29,
        lane_condition_version=1,
    )
    base.update(overrides)
    return ImpactState(**base)


def test_same_impact_yields_byte_for_byte_identical_result():
    impact = _impact(lateral_position_in=-1.5, heading_deg=3.0)
    a = MODEL.resolve(impact)
    b = MODEL.resolve(impact)
    assert a.fallen_pin_ids == b.fallen_pin_ids
    assert a.pins_knocked == b.pins_knocked


def test_fallen_ids_are_unique_in_range_and_match_the_count():
    for impact in (
        _impact(lateral_position_in=0.0, heading_deg=0.0),
        _impact(lateral_position_in=-2.0, heading_deg=4.5),
        _impact(lateral_position_in=2.5, heading_deg=-3.0),
    ):
        result = MODEL.resolve(impact)
        assert len(set(result.fallen_pin_ids)) == len(result.fallen_pin_ids)  # unique
        assert all(1 <= pin_id <= 10 for pin_id in result.fallen_pin_ids)
        assert result.pins_knocked == len(result.fallen_pin_ids)


def test_zero_speed_and_gutter_miss_knock_down_nothing():
    zero_speed = MODEL.resolve(_impact(speed_mph=0.0, lateral_position_in=5.0))
    assert zero_speed.pins_knocked == 0
    assert zero_speed.fallen_pin_ids == ()

    gutter = MODEL.resolve(_impact(lateral_position_in=GUTTER_ABS_LATERAL_IN + 5.0))
    assert gutter.pins_knocked == 0
    assert gutter.fallen_pin_ids == ()


def test_a_centered_impact_hits_the_headpin_and_stays_bounded():
    result = MODEL.resolve(_impact(lateral_position_in=0.0, heading_deg=0.0, speed_mph=16.0))
    assert 0 <= result.pins_knocked <= 10
    assert 1 in result.fallen_pin_ids  # a dead-straight shot must at least topple the pin it hits directly


def test_pairwise_resolution_leaves_no_overlap_and_never_gains_energy():
    # Deterministic set of approach configurations: same masses/radii as
    # the real model, varying approach speed and offset.
    configs = [
        dict(vx_a=50.0, vy_a=0.0, offset=(6.0, 0.0)),
        dict(vx_a=0.0, vy_a=80.0, offset=(0.0, 4.5)),
        dict(vx_a=-30.0, vy_a=40.0, offset=(4.0, 3.0)),
        dict(vx_a=15.0, vy_a=15.0, offset=(2.0, 2.0)),
    ]
    for cfg in configs:
        a = _Body(
            x_in=0.0, y_in=0.0, vx_in_s=cfg["vx_a"], vy_in_s=cfg["vy_a"],
            mass_lbs=15.0, radius_in=4.29, origin_x_in=0.0, origin_y_in=0.0, pin_id=0,
        )
        ox, oy = cfg["offset"]
        b = _Body(
            x_in=ox, y_in=oy, vx_in_s=0.0, vy_in_s=0.0,
            mass_lbs=3.5, radius_in=2.383, origin_x_in=ox, origin_y_in=oy, pin_id=1,
        )
        ke_before = 0.5 * a.mass_lbs * a.speed_in_s() ** 2 + 0.5 * b.mass_lbs * b.speed_in_s() ** 2

        _resolve_pair(a, b)

        post_dist = math.hypot(b.x_in - a.x_in, b.y_in - a.y_in)
        assert post_dist >= (a.radius_in + b.radius_in) - 1e-9

        ke_after = 0.5 * a.mass_lbs * a.speed_in_s() ** 2 + 0.5 * b.mass_lbs * b.speed_in_s() ** 2
        assert ke_after <= ke_before + 1e-9
        assert COLLISION_RESTITUTION <= 1.0  # the invariant the energy bound relies on


def test_simulation_always_stops_within_its_step_cap():
    fast_fallen, fast_steps = simulate_collision(_impact(speed_mph=16.0))
    assert fast_steps <= MAX_COLLISION_STEPS

    still_fallen, still_steps = simulate_collision(_impact(speed_mph=0.0))
    assert still_steps <= MAX_COLLISION_STEPS
    assert still_steps == 1  # every body starts below the settle threshold — stops immediately
    assert still_fallen == ()
