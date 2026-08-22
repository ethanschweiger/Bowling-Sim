"""Collision model + standing-pin selection: an explicit full rack matches
the default, a partial rack's result is always a subset with a matching
count, an empty rack is a clean no-op, and a single-pin rack can only ever
return that pin.
"""

import pytest

from app.physics.collision import PlanarCollisionPinfallModel, simulate_collision
from app.physics.impact import ImpactState
from app.physics.pin_deck import ALL_PIN_IDS, GUTTER_ABS_LATERAL_IN
from app.physics.rack import Rack, RackError

MALFORMED_SELECTIONS = (
    {11},        # unknown ID — must not silently become an empty deck
    [1, 1],      # duplicate
    {True},      # bool — must not silently become pin 1
    {1.0},       # float — must not silently become pin 1
    {"1"},       # string
    5,           # non-iterable
)

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


def test_explicit_full_rack_matches_the_default_result():
    impact = _impact(lateral_position_in=-1.0, heading_deg=2.0)
    default_result = MODEL.resolve(impact)
    explicit_result = MODEL.resolve(impact, standing_ids=ALL_PIN_IDS)
    assert explicit_result.fallen_pin_ids == default_result.fallen_pin_ids
    assert explicit_result.pins_knocked == default_result.pins_knocked

    rack_result = MODEL.resolve(impact, standing_ids=Rack.full().standing_ids)
    assert rack_result.fallen_pin_ids == default_result.fallen_pin_ids


def test_partial_rack_result_is_always_a_subset_with_matching_count():
    impact = _impact(lateral_position_in=0.0, heading_deg=0.0)
    for standing in (
        frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10}),
        frozenset({1, 2, 3}),
        frozenset({4, 5, 6}),
        frozenset({7, 8, 9, 10}),
        frozenset({1}),
        frozenset(),
    ):
        result = MODEL.resolve(impact, standing_ids=standing)
        assert set(result.fallen_pin_ids) <= standing
        assert result.pins_knocked == len(result.fallen_pin_ids)


def test_empty_rack_returns_zero_without_simulating_a_phantom_pin():
    impact = _impact(lateral_position_in=0.0, heading_deg=0.0)
    result = MODEL.resolve(impact, standing_ids=frozenset())
    assert result.pins_knocked == 0
    assert result.fallen_pin_ids == ()


def test_centered_impact_against_only_the_headpin_can_only_knock_pin_1():
    impact = _impact(lateral_position_in=0.0, heading_deg=0.0, speed_mph=16.0)
    result = MODEL.resolve(impact, standing_ids=frozenset({1}))
    assert set(result.fallen_pin_ids) <= {1}
    # A dead-center straight shot at the headpin, with no other pins even
    # present to deflect it, must topple the one pin it directly hits.
    assert result.fallen_pin_ids == (1,)
    assert result.pins_knocked == 1


def test_no_id_absent_from_the_rack_can_ever_be_returned():
    impact = _impact(lateral_position_in=1.5, heading_deg=3.0, speed_mph=18.0)
    standing = frozenset({2, 4, 6, 8, 10})
    result = MODEL.resolve(impact, standing_ids=standing)
    assert set(result.fallen_pin_ids).isdisjoint({1, 3, 5, 7, 9})
    assert set(result.fallen_pin_ids) <= standing


# --- Validation hardening -------------------------------------------------


def test_planar_selection_rejects_malformed_ids_with_rack_error():
    impact = _impact()
    for bad in MALFORMED_SELECTIONS:
        with pytest.raises(RackError):
            MODEL.resolve(impact, standing_ids=bad)


def test_planar_selection_rejects_malformed_ids_even_on_a_gutter_miss():
    # Beyond the lane edge — resolve()'s gutter check would otherwise
    # return before standing_ids is ever looked at.
    gutter_impact = _impact(lateral_position_in=GUTTER_ABS_LATERAL_IN + 5.0)
    for bad in MALFORMED_SELECTIONS:
        with pytest.raises(RackError):
            MODEL.resolve(gutter_impact, standing_ids=bad)

    # A valid selection on a gutter miss still behaves exactly as before:
    # zero pins, no error.
    result = MODEL.resolve(gutter_impact, standing_ids={1, 2, 3})
    assert result.pins_knocked == 0
    assert result.fallen_pin_ids == ()


def test_simulate_collision_rejects_malformed_ids_even_at_non_positive_speed():
    # simulate_collision's non-positive-speed short circuit would
    # otherwise return before standing_ids is ever looked at — and it can
    # be called directly, without going through resolve() at all.
    for speed in (0.0, -5.0):
        still_impact = _impact(speed_mph=speed)
        for bad in MALFORMED_SELECTIONS:
            with pytest.raises(RackError):
                simulate_collision(still_impact, standing_ids=bad)

        # A valid (including empty) selection at non-positive speed still
        # returns the same zero/default no-op as before, no error.
        fallen, steps = simulate_collision(still_impact, standing_ids=frozenset())
        assert fallen == () and steps == 0
        fallen, steps = simulate_collision(still_impact, standing_ids=None)
        assert fallen == () and steps == 0


def test_omitted_default_and_validated_selections_still_behave_as_before():
    impact = _impact(lateral_position_in=-1.0, heading_deg=2.0)
    default_result = MODEL.resolve(impact)
    explicit_full = MODEL.resolve(impact, standing_ids=ALL_PIN_IDS)
    assert explicit_full.fallen_pin_ids == default_result.fallen_pin_ids

    partial = MODEL.resolve(impact, standing_ids={1, 2, 3})
    assert set(partial.fallen_pin_ids) <= {1, 2, 3}

    empty = MODEL.resolve(impact, standing_ids=frozenset())
    assert empty.pins_knocked == 0 and empty.fallen_pin_ids == ()
