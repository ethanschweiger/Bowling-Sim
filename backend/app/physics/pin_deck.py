"""Standard ten-pin deck geometry — pure, immutable, and independent of any
bowler's handedness.

## Coordinate system

Consistent with the rest of the physics package (see simulate.py's
"Coordinate conventions"):

- **Downlane distance** (`distance_ft`) from the foul line — the same axis
  a trajectory's `distance_ft` uses. The No. 1 pin sits at 60 ft.
- **Lateral position** (`lateral_in`) in inches from lane center (board 20
  of 39 — the lane's exact geometric center), positive toward higher board
  numbers. This is a property of the *lane*, not of any bowler: nothing
  here reads or depends on handedness. (For a right-handed bowler this
  happens to be a drift to the left, per simulate.py's convention, but the
  geometry itself doesn't know or care.)

## Sources

Every USBC-cited number below comes from the official Equipment
Specifications and Certifications Manual (bowl.com), current revision
"Last updated on 03/26" (March 2026), accessed 2026-08-22:
https://images.bowl.com/bowl/media/assets/usbc/equipment%20specs/26_231-26-march-es-manual.pdf
— specifically the "Bowling Pin Dimensions" (target/min/max) and "Pin
Spots" sections. Figures are quoted as printed (target, with the manual's
own min/max tolerance either side), not re-derived or rounded further.
Re-verified against the manual's prior revision too: every figure below
is unchanged across both.

`USBC_PIN_MAX_DIAMETER_IN` is cited for completeness but is deliberately
**not** turned into an effective 2D collision radius *here* — a pin's real
cross-section varies with height, and picking one number to represent it
in a flat model is a calibration decision that belongs to whatever model
actually consumes it (see `collision.py`), not to this pure-geometry module.
"""

import math
from dataclasses import dataclass

from app.physics.lane import BOARD_COUNT
from app.physics.units import BOARD_WIDTH_IN

# --- Geometry -----------------------------------------------------------

HEADPIN_DISTANCE_FT = 60.0  # "60 feet, +/- 1/2 inch, from the foul line to the center of the No. 1 pin spot"
PIN_SPACING_IN = 12.0       # "Spaced 12 inches, +/- 1/16 inch, (non accumulative) in an equilateral triangle"
ROW_SPACING_IN = PIN_SPACING_IN * math.sqrt(3) / 2  # equilateral triangle row height, ~10.392 in

# Board 20 of 39 is the lane's exact geometric center (19 boards to a side).
LANE_CENTER_BOARD = (BOARD_COUNT + 1) / 2

# Board 0 and board 40 — the lane's edges — in the same inches-from-center
# coordinate `Pin.lateral_in` uses. A ball landing at or beyond this is off
# the lane entirely, never mind the pin deck. Shared by every pinfall model
# so "off the lane" means the same thing everywhere.
GUTTER_ABS_LATERAL_IN = LANE_CENTER_BOARD * BOARD_WIDTH_IN

# --- USBC reference constants for a future collision model --------------
# Cited from the manual's pin dimension table: (target, minimum, maximum).

USBC_PIN_WEIGHT_OZ = (56.0, 54.0, 58.0)          # 3 lb 8 oz target; 3 lb 6 oz - 3 lb 10 oz range
USBC_PIN_HEIGHT_IN = (15.000, 14.969, 15.031)
# The pin's widest diameter — its "belly" — occurs 4.5 in above the base,
# per the manual's pin-profile table (NOT the base itself, which is only
# 2.031 in). NOT a 2D collision radius here — see module docstring.
USBC_PIN_MAX_DIAMETER_IN = (4.766, 4.735, 4.797)
USBC_PIN_COEFFICIENT_OF_RESTITUTION = (0.670, 0.605, 0.735)


@dataclass(frozen=True)
class Pin:
    id: int            # 1-10, standard USBC numbering
    row: int            # 0 (headpin) .. 3 (back row)
    lateral_in: float   # inches from lane center; + toward higher board numbers
    distance_ft: float  # downlane distance from the foul line


def _build_standard_deck() -> tuple[Pin, ...]:
    # (pin_id, row, lateral position in units of half a pin-spacing)
    # Matches the standard diagram, viewed from the foul line:
    #     7  8  9  10
    #       4  5  6
    #         2  3
    #           1
    # Board 1 is the right gutter (see simulate.py), so pin 10 (rightmost)
    # is the negative-lateral side and pin 7 (leftmost) the positive side.
    layout = (
        (1, 0, 0),
        (2, 1, 1), (3, 1, -1),
        (4, 2, 2), (5, 2, 0), (6, 2, -2),
        (7, 3, 3), (8, 3, 1), (9, 3, -1), (10, 3, -3),
    )
    pins = []
    for pin_id, row, half_spacings in layout:
        lateral_in = half_spacings * (PIN_SPACING_IN / 2)
        distance_ft = HEADPIN_DISTANCE_FT + row * (ROW_SPACING_IN / 12.0)
        pins.append(Pin(id=pin_id, row=row, lateral_in=lateral_in, distance_ft=distance_ft))
    return tuple(pins)


STANDARD_DECK: tuple[Pin, ...] = _build_standard_deck()

# The full set of valid pin IDs — the shared definition of "a fresh rack"
# (app/physics/rack.py) and of every pin a collision model can be asked to
# simulate (app/physics/collision.py).
ALL_PIN_IDS: frozenset = frozenset(pin.id for pin in STANDARD_DECK)
