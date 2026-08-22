"""Standard ten-pin deck geometry: unique IDs, correct triangular rows, and
exact 12-inch spacing between every adjacent pair in the lattice.
"""

import math

import pytest

from app.physics.pin_deck import PIN_SPACING_IN, STANDARD_DECK


def test_deck_has_ten_unique_pin_ids():
    ids = [pin.id for pin in STANDARD_DECK]
    assert sorted(ids) == list(range(1, 11))


def test_deck_has_the_correct_triangular_rows():
    rows = {0: {1}, 1: {2, 3}, 2: {4, 5, 6}, 3: {7, 8, 9, 10}}
    by_row = {}
    for pin in STANDARD_DECK:
        by_row.setdefault(pin.row, set()).add(pin.id)
    assert by_row == rows


def test_every_adjacent_pin_pair_is_12_inches_apart():
    pins = {pin.id: pin for pin in STANDARD_DECK}
    half_step = PIN_SPACING_IN / 2

    def half_spacings(pin):
        return round(pin.lateral_in / half_step)

    ids = sorted(pins)
    adjacent_pairs = []
    for i in ids:
        for j in ids:
            if i >= j:
                continue
            pi, pj = pins[i], pins[j]
            row_diff = abs(pi.row - pj.row)
            half_diff = abs(half_spacings(pi) - half_spacings(pj))
            same_row_adjacent = row_diff == 0 and half_diff == 2
            cross_row_adjacent = row_diff == 1 and half_diff == 1
            if same_row_adjacent or cross_row_adjacent:
                adjacent_pairs.append((i, j))

    # 6 same-row neighbor pairs + 12 cross-row neighbor pairs in this lattice.
    assert len(adjacent_pairs) == 18

    for i, j in adjacent_pairs:
        pi, pj = pins[i], pins[j]
        lateral_diff_in = pi.lateral_in - pj.lateral_in
        downlane_diff_in = (pi.distance_ft - pj.distance_ft) * 12.0
        distance_in = math.hypot(lateral_diff_in, downlane_diff_in)
        assert distance_in == pytest.approx(12.0), f"pins {i}-{j} are {distance_in:.4f} in apart"
