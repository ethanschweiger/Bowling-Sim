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

Mass gets the same treatment as everything else here. A ball or pin spec
sheet states a *weight* in pounds (lbf, a force) — it is not an inertial
mass, even though `Ball.mass_lbs` and the USBC pin weight are colloquially
called "mass" throughout this codebase. `collision.py` is the one place
that treats mass as a genuine physical quantity (impulse and kinetic
energy both need real inertia, not force), so it's the one place that
converts a stated weight through standard gravity into a true mass via
`weight_lbf_to_mass_blob` before doing any of that math.
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


# Standard gravity, in inches/second^2 (32.174 ft/s^2 * 12 in/ft). This is
# the conversion factor between a stated weight (lbf, force) and a true
# inertial mass, in the inch-pound-second consistent unit sometimes called
# a "blob" or "slinch" (1 blob = 1 lbf*s^2/in = 12 slugs). Using mass in
# this unit makes F=ma and KE=0.5*m*v^2 dimensionally correct when force
# is in lbf and length is in inches — the unit system collision.py works in.
STANDARD_GRAVITY_FT_PER_S2 = 32.174
STANDARD_GRAVITY_IN_PER_S2 = STANDARD_GRAVITY_FT_PER_S2 * IN_PER_FT


def weight_lbf_to_mass_blob(weight_lbf: float) -> float:
    """Converts a stated weight (lbf — what a ball or pin spec sheet gives)
    into a true inertial mass. Applying this to every weight before an
    impulse or kinetic-energy calculation, consistently, leaves mass
    *ratios* between objects unchanged (both are scaled by the same
    gravity constant) — only the units become physically honest.
    """
    return weight_lbf / STANDARD_GRAVITY_IN_PER_S2
