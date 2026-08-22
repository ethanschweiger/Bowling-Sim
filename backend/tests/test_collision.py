"""Deterministic 2D pin-collision model: reproducibility, ID validity,
zero-speed/gutter safety (including overlapping starting positions), a
centered strike-line hit, pairwise impulse correctness (no overlap, no
energy gain, including the exact-coincident fallback), the mass-conversion
ratio invariant, and bounded termination.
"""

import math

import pytest

from app.physics.collision import (
    COLLISION_RESTITUTION,
    MAX_COLLISION_STEPS,
    PIN_MASS_BLOB,
    PIN_WEIGHT_LBF,
    PlanarCollisionPinfallModel,
    _Body,
    _resolve_pair,
    simulate_collision,
)
from app.physics.impact import ImpactState
from app.physics.pin_deck import GUTTER_ABS_LATERAL_IN
from app.physics.units import weight_lbf_to_mass_blob

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


def test_zero_speed_at_center_and_overlapping_offsets_knock_down_nothing():
    # lateral_position_in=0.0 puts the ball exactly on top of the headpin
    # (both at distance 0 from lane center / the headpin plane) — the
    # worst-case overlap. A handful of other offsets land the (stationary)
    # ball's circle partially over the headpin's without being exactly
    # coincident with it.
    for lateral_in in (0.0, 1.0, -1.5, 3.0, -3.0):
        result = MODEL.resolve(_impact(speed_mph=0.0, lateral_position_in=lateral_in))
        assert result.pins_knocked == 0
        assert result.fallen_pin_ids == ()

    gutter = MODEL.resolve(_impact(lateral_position_in=GUTTER_ABS_LATERAL_IN + 5.0))
    assert gutter.pins_knocked == 0
    assert gutter.fallen_pin_ids == ()


def test_a_centered_impact_hits_the_headpin_and_stays_bounded():
    result = MODEL.resolve(_impact(lateral_position_in=0.0, heading_deg=0.0, speed_mph=16.0))
    assert 0 <= result.pins_knocked <= 10
    assert 1 in result.fallen_pin_ids  # a dead-straight shot must at least topple the pin it hits directly


def _kinetic_energy(*bodies) -> float:
    return sum(0.5 * body.mass_blob * body.speed_in_s() ** 2 for body in bodies)


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
            mass_blob=weight_lbf_to_mass_blob(15.0), radius_in=4.29,
            origin_x_in=0.0, origin_y_in=0.0, pin_id=0,
        )
        ox, oy = cfg["offset"]
        b = _Body(
            x_in=ox, y_in=oy, vx_in_s=0.0, vy_in_s=0.0,
            mass_blob=PIN_MASS_BLOB, radius_in=2.383,
            origin_x_in=ox, origin_y_in=oy, pin_id=1,
        )
        ke_before = _kinetic_energy(a, b)

        _resolve_pair(a, b)

        post_dist = math.hypot(b.x_in - a.x_in, b.y_in - a.y_in)
        assert post_dist >= (a.radius_in + b.radius_in) - 1e-9

        ke_after = _kinetic_energy(a, b)
        assert ke_after <= ke_before + 1e-9
        assert COLLISION_RESTITUTION <= 1.0  # the invariant the energy bound relies on


def test_exact_coincident_bodies_separate_deterministically_without_energy_gain():
    # Case 1: nonzero relative velocity — separates along that direction.
    moving = _Body(
        x_in=5.0, y_in=5.0, vx_in_s=20.0, vy_in_s=0.0,
        mass_blob=weight_lbf_to_mass_blob(15.0), radius_in=4.29,
        origin_x_in=5.0, origin_y_in=5.0, pin_id=0,
    )
    still = _Body(
        x_in=5.0, y_in=5.0, vx_in_s=0.0, vy_in_s=0.0,
        mass_blob=PIN_MASS_BLOB, radius_in=2.383,
        origin_x_in=5.0, origin_y_in=5.0, pin_id=1,
    )
    ke_before = _kinetic_energy(moving, still)
    _resolve_pair(moving, still)
    dist = math.hypot(still.x_in - moving.x_in, still.y_in - moving.y_in)
    assert dist == pytest.approx(moving.radius_in + still.radius_in, abs=1e-9)
    assert _kinetic_energy(moving, still) <= ke_before + 1e-9

    # Case 2: both exactly stationary — falls back to the fixed +y axis,
    # deterministically, still without adding energy (there was none).
    a = _Body(
        x_in=1.0, y_in=1.0, vx_in_s=0.0, vy_in_s=0.0,
        mass_blob=weight_lbf_to_mass_blob(15.0), radius_in=4.29,
        origin_x_in=1.0, origin_y_in=1.0, pin_id=0,
    )
    b = _Body(
        x_in=1.0, y_in=1.0, vx_in_s=0.0, vy_in_s=0.0,
        mass_blob=PIN_MASS_BLOB, radius_in=2.383,
        origin_x_in=1.0, origin_y_in=1.0, pin_id=1,
    )
    _resolve_pair(a, b)
    assert a.x_in == pytest.approx(1.0)  # separated purely along y (the fixed fallback axis)
    assert b.x_in == pytest.approx(1.0)
    assert b.y_in - a.y_in == pytest.approx(a.radius_in + b.radius_in, abs=1e-9)
    assert _kinetic_energy(a, b) == 0.0

    # Running it again from scratch gives the identical separation — deterministic.
    a2 = _Body(
        x_in=1.0, y_in=1.0, vx_in_s=0.0, vy_in_s=0.0,
        mass_blob=weight_lbf_to_mass_blob(15.0), radius_in=4.29,
        origin_x_in=1.0, origin_y_in=1.0, pin_id=0,
    )
    b2 = _Body(
        x_in=1.0, y_in=1.0, vx_in_s=0.0, vy_in_s=0.0,
        mass_blob=PIN_MASS_BLOB, radius_in=2.383,
        origin_x_in=1.0, origin_y_in=1.0, pin_id=1,
    )
    _resolve_pair(a2, b2)
    assert (a2.x_in, a2.y_in) == (a.x_in, a.y_in)
    assert (b2.x_in, b2.y_in) == (b.x_in, b.y_in)


def test_mass_conversion_preserves_the_ball_to_pin_weight_ratio():
    ball_weight_lbf = 15.0
    ball_mass_blob = weight_lbf_to_mass_blob(ball_weight_lbf)

    # Both weights go through the identical gravity conversion, so the
    # mass ratio equals the weight ratio exactly.
    assert ball_mass_blob / PIN_MASS_BLOB == pytest.approx(ball_weight_lbf / PIN_WEIGHT_LBF)

    # And the conversion is a straight division by one shared constant —
    # not some per-object scheme that could drift apart.
    from app.physics.units import STANDARD_GRAVITY_IN_PER_S2

    assert ball_mass_blob == pytest.approx(ball_weight_lbf / STANDARD_GRAVITY_IN_PER_S2)
    assert PIN_MASS_BLOB == pytest.approx(PIN_WEIGHT_LBF / STANDARD_GRAVITY_IN_PER_S2)


def test_simulation_always_stops_within_its_step_cap():
    fast_fallen, fast_steps = simulate_collision(_impact(speed_mph=16.0))
    assert fast_steps <= MAX_COLLISION_STEPS

    still_fallen, still_steps = simulate_collision(_impact(speed_mph=0.0))
    assert still_steps == 0  # non-positive speed short-circuits before any step runs
    assert still_fallen == ()
