"""The one declared unit system for the integration loop.

The simulator steps through a throw in feet and seconds. A bowler or API
client thinks in mph and RPM; those get converted to the feet/seconds basis
exactly once, at the boundary, and converted back to mph only when a result
leaves the simulator. Nothing inside the integration loop mixes units —
`simulate.py` never divides feet by mph, or multiplies a feet-per-second
quantity by a raw RPM number.

Lateral motion gets the same treatment. A board number is a position on a
39-board index, not a length — it takes a declared board width to convert
between the two. `simulate.py` accumulates lateral displacement in feet
(a real length) throughout the throw and converts to a board number only
once, at the trajectory/API boundary, via `ft_to_boards`/`boards_to_ft`.
"""

import math

FT_PER_MILE = 5280.0
SECONDS_PER_HOUR = 3600.0
MPH_TO_FPS = FT_PER_MILE / SECONDS_PER_HOUR  # 1 mph == ~1.4667 ft/s

SECONDS_PER_MINUTE = 60.0
RADIANS_PER_REV = 2 * math.pi

IN_PER_FT = 12.0
# Declared board width: a regulation 39-board lane runs about 41.5 inches
# across. We use 1.05 in/board as the stated modeling figure this simulator
# works from, rather than the exact (and not meaningfully different)
# 41.5 / 39 = ~1.064 in/board.
BOARD_WIDTH_IN = 1.05
BOARD_WIDTH_FT = BOARD_WIDTH_IN / IN_PER_FT


def mph_to_fps(mph: float) -> float:
    return mph * MPH_TO_FPS


def fps_to_mph(fps: float) -> float:
    return fps / MPH_TO_FPS


def mph_to_in_per_s(mph: float) -> float:
    return mph_to_fps(mph) * IN_PER_FT


def rpm_to_rad_per_s(rpm: float) -> float:
    return rpm * RADIANS_PER_REV / SECONDS_PER_MINUTE


def ft_to_boards(feet: float) -> float:
    return feet / BOARD_WIDTH_FT


def boards_to_ft(boards: float) -> float:
    return boards * BOARD_WIDTH_FT


def boards_to_in(boards: float) -> float:
    return boards * BOARD_WIDTH_IN
