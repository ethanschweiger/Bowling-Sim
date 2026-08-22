"""The one declared unit system for the integration loop.

The simulator steps through a throw in feet and seconds. A bowler or API
client thinks in mph and RPM; those get converted to the feet/seconds basis
exactly once, at the boundary, and converted back to mph only when a result
leaves the simulator. Nothing inside the integration loop mixes units —
`simulate.py` never divides feet by mph, or multiplies a feet-per-second
quantity by a raw RPM number.
"""

import math

FT_PER_MILE = 5280.0
SECONDS_PER_HOUR = 3600.0
MPH_TO_FPS = FT_PER_MILE / SECONDS_PER_HOUR  # 1 mph == ~1.4667 ft/s

SECONDS_PER_MINUTE = 60.0
RADIANS_PER_REV = 2 * math.pi


def mph_to_fps(mph: float) -> float:
    return mph * MPH_TO_FPS


def fps_to_mph(fps: float) -> float:
    return fps / MPH_TO_FPS


def rpm_to_rad_per_s(rpm: float) -> float:
    return rpm * RADIANS_PER_REV / SECONDS_PER_MINUTE
