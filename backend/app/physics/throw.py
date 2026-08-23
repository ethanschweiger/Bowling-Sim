"""What the bowler controls at the foul line, plus the small, human release
error that shows up even when a bowler is trying to repeat a shot exactly.
"""

# Keeps the modern `X | None` annotation spelling usable on this project's
# Python 3.9 runtime floor: with this import, annotations are never
# evaluated at class/function-definition time, only read as strings (by
# a human, or by an explicit `typing.get_type_hints` call this codebase
# never makes) — so the `|` operator here doesn't need Python 3.10's
# support for it on plain types. Pydantic models are the one exception:
# they resolve annotations eagerly regardless of this import, so
# app/models/schemas.py still spells optional fields with typing.Optional.
from __future__ import annotations

import random
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Throw:
    speed_mph: float = 17.0        # ball speed off the hand
    rev_rate: float = 350.0        # revolutions per minute
    axis_rotation: float = 45.0    # degrees; 0 = full roll, 90 = full spinner
    axis_tilt: float = 15.0        # degrees; higher tilt = more skid, later hook
    launch_angle: float = -1.5     # right-handed starter line: aimed toward lower/right boards
    # the ball's laydown board at the foul line, 1-39 — NOT the bowler's stance board
    launch_position: float = 28.0


# One standard deviation of release error per field, in the field's own
# units. These are deliberately a *narrow*, repeatable house-shot profile,
# not measured bowler data. In particular, this planar model holds the launch
# heading for the full 60 ft, so even a modest angle error accumulates into
# several boards downlane. Keep angle and laydown variance tight enough that
# a repeated requested release stays recognizably the same line while seeds
# still produce a visible, bounded difference.
_RELEASE_NOISE_STD = {
    "speed_mph": 0.10,
    "rev_rate": 5.0,
    "axis_rotation": 0.75,
    "axis_tilt": 0.75,
    "launch_angle": 0.05,
    "launch_position": 0.15,
}

# Hard clip on the sampled noise so a rare draw can't wander outside what a
# human release plausibly looks like.
_NOISE_CLAMP_STD = 3.0

# The same legal range the API enforces on a *requested* throw (see
# ThrowRequest in app/models/schemas.py, which imports this dict directly
# so the two can't drift apart). A sampled release is clamped into this
# range too: a valid request can never come out the other side of sampling
# with a negative rev rate, a sub-minimum speed, or a board/axis value that
# doesn't exist.
RELEASE_BOUNDS = {
    "speed_mph": (10.0, 25.0),
    "rev_rate": (0.0, 600.0),
    "axis_rotation": (0.0, 90.0),
    "axis_tilt": (0.0, 90.0),
    # Tighter than the model's original +-10 degrees: launch_angle is a
    # *sustained* heading held for the whole 60 ft in this simplified model
    # (nothing decays it back toward zero), and with the corrected board
    # width (see units.py), even a couple of degrees integrates into tens
    # of boards of drift. +-2 degrees keeps the parameter physically
    # plausible for a real release angle under that assumption.
    "launch_angle": (-2.0, 2.0),
    "launch_position": (1.0, 39.0),
}


def sample_release(requested: Throw, seed: int | None = None) -> tuple[Throw, int]:
    """Sample a small, bounded release error around the requested throw.

    The same seed always reproduces the same sampled throw. If no seed is
    given, one is generated and returned so the caller can replay this exact
    throw later. The sampled result is always clamped to `RELEASE_BOUNDS`,
    so replaying a seed stays reproducible even at the edge of legal range.
    """
    if seed is None:
        seed = random.SystemRandom().randrange(2**31)

    rng = random.Random(seed)
    sampled_fields = {}
    for field, std in _RELEASE_NOISE_STD.items():
        noise = rng.gauss(0.0, std)
        noise = max(-_NOISE_CLAMP_STD * std, min(_NOISE_CLAMP_STD * std, noise))
        lo, hi = RELEASE_BOUNDS[field]
        sampled_fields[field] = max(lo, min(hi, getattr(requested, field) + noise))

    return replace(requested, **sampled_fields), seed
