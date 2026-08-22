"""The standing-pin rack: a fresh rack, applying a valid fallen set,
reset, and rejecting malformed/duplicate/non-standing updates without
mutating the prior rack.
"""

import pytest

from app.physics.pin_deck import ALL_PIN_IDS
from app.physics.rack import Rack, RackError


def test_fresh_rack_exposes_all_ten_ids():
    rack = Rack.full()
    assert rack.standing_ids == ALL_PIN_IDS
    assert len(rack) == 10
    assert all(pin_id in rack for pin_id in range(1, 11))


def test_removing_a_valid_fallen_set_leaves_exactly_the_complement():
    rack = Rack.full()
    updated = rack.after_fallen([1, 2, 3])
    assert updated.standing_ids == frozenset(range(4, 11))
    assert 1 not in updated and 2 not in updated and 3 not in updated
    assert 4 in updated

    # The original rack is untouched — after_fallen returns a new Rack.
    assert rack.standing_ids == ALL_PIN_IDS


def test_reset_returns_a_fresh_all_ten_rack():
    rack = Rack.full().after_fallen([7, 8, 9, 10])
    assert len(rack) == 6
    assert rack.reset().standing_ids == ALL_PIN_IDS


def test_out_of_range_id_is_rejected_without_changing_the_rack():
    rack = Rack.full()
    with pytest.raises(RackError):
        rack.after_fallen([11])
    with pytest.raises(RackError):
        rack.after_fallen([0])
    with pytest.raises(RackError):
        rack.after_fallen([-1])
    assert rack.standing_ids == ALL_PIN_IDS


def test_duplicate_fallen_id_is_rejected_without_changing_the_rack():
    rack = Rack.full()
    with pytest.raises(RackError):
        rack.after_fallen([1, 1])
    assert rack.standing_ids == ALL_PIN_IDS


def test_non_standing_id_is_rejected_without_changing_the_rack():
    rack = Rack.full().after_fallen([1])
    assert 1 not in rack

    with pytest.raises(RackError):
        rack.after_fallen([1])  # already down — can't fall again

    # Rejection didn't touch the rack it was called on.
    assert rack.standing_ids == frozenset(range(2, 11))


def test_direct_construction_with_invalid_ids_is_rejected():
    with pytest.raises(RackError):
        Rack(standing_ids=frozenset({1, 2, 11}))


def test_emptying_the_rack_via_after_fallen_is_legal():
    rack = Rack.full()
    empty = rack.after_fallen(range(1, 11))
    assert len(empty) == 0
    assert empty.standing_ids == frozenset()
