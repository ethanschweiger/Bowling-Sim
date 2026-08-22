"""What the bowler controls at the foul line, plus the small, human release
error that shows up even when a bowler is trying to repeat a shot exactly.
"""

import random
from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class Throw:
    speed_mph: float = 17.0        # ball speed off the hand
    rev_rate: float = 350.0        # revolutions per minute
    axis_rotation: float = 45.0    # degrees; 0 = full roll, 90 = full spinner
    axis_tilt: float = 15.0        # degrees; higher tilt = more skid, later hook
    launch_angle: float = 2.0      # degrees off the lane's centerline at release
    launch_position: float = 28.0  # starting board, 1-39 (right-handers start ~28-30)


# One standard deviation of release error per field, in the field's own
# units. These approximate what a consistent amateur/league bowler varies
# shot to shot — not a beginner's spread, not a pro's.
_RELEASE_NOISE_STD = {
    "speed_mph": 0.25,
    "rev_rate": 12.0,
    "axis_rotation": 2.0,
    "axis_tilt": 2.0,
    "launch_angle": 0.3,
    "launch_position": 0.4,
}

# Hard clip on the sampled noise so a rare draw can't wander outside what a
# human release plausibly looks like.
_NOISE_CLAMP_STD = 3.0


def sample_release(requested: Throw, seed: Optional[int] = None) -> tuple[Throw, int]:
    """Sample a small, bounded release error around the requested throw.

    The same seed always reproduces the same sampled throw. If no seed is
    given, one is generated and returned so the caller can replay this exact
    throw later.
    """
    if seed is None:
        seed = random.SystemRandom().randrange(2**31)

    rng = random.Random(seed)
    sampled_fields = {}
    for field, std in _RELEASE_NOISE_STD.items():
        noise = rng.gauss(0.0, std)
        noise = max(-_NOISE_CLAMP_STD * std, min(_NOISE_CLAMP_STD * std, noise))
        sampled_fields[field] = getattr(requested, field) + noise

    return replace(requested, **sampled_fields), seed
