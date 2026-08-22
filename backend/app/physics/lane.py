"""Lane and oil pattern model.

The lane is 60 feet from foul line to headpin, 39 boards wide. `friction_at`
returns a friction coefficient for a (distance, board) pair: low on oil,
high on dry wood. The simulator reads this map one step at a time so a ball
gradually loses skid and starts to hook as it runs out of oil, same as a
real throw.
"""

from dataclasses import dataclass, field

BOARD_COUNT = 39
LANE_LENGTH_FT = 60.0

OILED_FRICTION = 0.015   # low friction — the ball skids
DRY_FRICTION = 0.080     # high friction — the ball grips and hooks


@dataclass(frozen=True)
class OilPattern:
    name: str
    length_ft: float          # how far down the lane the oil extends
    volume_ml: float           # total oil laid down — informational for now
    center_boards: tuple[int, int]  # inclusive board range carrying the heaviest oil


# Two starter patterns: a forgiving house shot and a flatter sport pattern.
OIL_PATTERNS: dict[str, OilPattern] = {
    "house": OilPattern(name="House Shot", length_ft=32.0, volume_ml=22.0, center_boards=(8, 32)),
    "sport": OilPattern(name="Sport Pattern", length_ft=42.0, volume_ml=24.0, center_boards=(1, 39)),
}


@dataclass(frozen=True)
class Lane:
    oil_pattern: OilPattern
    temperature_f: float = 72.0
    board_count: int = BOARD_COUNT
    length_ft: float = LANE_LENGTH_FT

    def friction_at(self, distance_ft: float, board: float) -> float:
        """Friction coefficient at a point on the lane.

        Inside the oil pattern's length and board range: low friction.
        Past the oil, or outside the oiled boards: dry, high friction.
        The transition is a smooth ramp rather than a hard edge, since real
        oil patterns taper off instead of stopping dead.
        """
        pattern = self.oil_pattern
        low, high = pattern.center_boards
        on_oiled_boards = low - 1 <= board <= high + 1

        if distance_ft >= pattern.length_ft or not on_oiled_boards:
            return DRY_FRICTION

        # Ramp friction up over the last 6 feet of the pattern so the
        # transition to dry boards isn't a cliff.
        taper_start = max(0.0, pattern.length_ft - 6.0)
        if distance_ft <= taper_start:
            return OILED_FRICTION

        progress = (distance_ft - taper_start) / (pattern.length_ft - taper_start)
        return OILED_FRICTION + progress * (DRY_FRICTION - OILED_FRICTION)
