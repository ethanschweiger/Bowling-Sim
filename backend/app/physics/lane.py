"""Lane and oil pattern model.

The lane is 60 feet from foul line to headpin, 39 boards wide. Oil sits on
top of it as a grid: one amount per (board, foot-of-distance) cell. Friction
at a point is derived from how much oil remains in that cell — heavy oil
means low friction and skid, no oil means high friction and hook.

The grid is the lane's *condition*, not a fixed pattern: `apply_wear` reads
a completed throw's path and returns a new condition with oil picked up
along that path and a little carried down-lane, the way a real ball wears
in a pattern over a game.
"""

from dataclasses import dataclass

BOARD_COUNT = 39
LANE_LENGTH_FT = 60.0
GRID_RESOLUTION_FT = 1.0
GRID_LENGTH_CELLS = int(LANE_LENGTH_FT / GRID_RESOLUTION_FT) + 1  # feet 0..60 inclusive

OILED_FRICTION = 0.015   # heavy oil — the ball skids
DRY_FRICTION = 0.080     # no oil — the ball grips and hooks

# How much of a cell's oil a single pass picks up, and how much of that
# picked-up oil resurfaces further down the lane. Both are small fractions
# so wear is gradual and one throw can never dry out a board.
PICKUP_FRACTION = 0.04
CARRYDOWN_FRACTION = 0.01
CARRYDOWN_OFFSET_FT = 4.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class OilPatternSpec:
    """The stated shape of a pattern: how far it runs, how it tapers, and
    the lateral volume ratio between the boards it favors and the boards
    it barely touches."""

    name: str
    length_ft: float                  # distance the pattern extends down the lane
    taper_ft: float                   # feet over which volume ramps to zero at the end
    center_boards: tuple[int, int]    # inclusive board range carrying full (peak) volume
    total_boards: tuple[int, int]     # inclusive board range carrying any oil at all
    pattern_ratio: float              # peak-board volume : edge-of-pattern volume
    peak_ml_per_ft: float             # oil laid per foot of lane on the peak boards


# A standard house shot: forgiving, oil concentrated in the center of the
# lane, roughly a 3:1 ratio between the peak boards and the pattern's outer
# edge, 32 feet long with a 6-foot taper into the dry back end.
HOUSE_SHOT_SPEC = OilPatternSpec(
    name="House Shot",
    length_ft=32.0,
    taper_ft=6.0,
    center_boards=(8, 32),
    total_boards=(3, 37),
    pattern_ratio=3.0,
    peak_ml_per_ft=0.7,
)


def _lateral_factor(board: int, spec: OilPatternSpec) -> float:
    """0..1 oil multiplier for a board: 1.0 across the center boards, ramping
    down to 1/pattern_ratio at the pattern's outer edge, 0 past it."""
    low_total, high_total = spec.total_boards
    low_center, high_center = spec.center_boards

    if board < low_total or board > high_total:
        return 0.0
    if low_center <= board <= high_center:
        return 1.0

    edge_floor = 1.0 / spec.pattern_ratio
    if board < low_center:
        span = max(low_center - low_total, 1)
        progress = (board - low_total) / span
    else:
        span = max(high_total - high_center, 1)
        progress = (high_total - board) / span
    return edge_floor + progress * (1.0 - edge_floor)


def _longitudinal_factor(distance_ft: float, spec: OilPatternSpec) -> float:
    """0..1 oil multiplier along the lane's length: full volume until the
    taper starts, linearly down to zero at the pattern's stated length."""
    taper_start = max(0.0, spec.length_ft - spec.taper_ft)
    if distance_ft >= spec.length_ft:
        return 0.0
    if distance_ft <= taper_start:
        return 1.0
    return (spec.length_ft - distance_ft) / (spec.length_ft - taper_start)


@dataclass(frozen=True)
class LaneCondition:
    """A snapshot of oil on the lane. Immutable — wear produces a new
    snapshot rather than mutating this one, so the physics layer stays pure.
    """

    spec: OilPatternSpec
    oil_grid: tuple[tuple[float, ...], ...]  # [board 1..39][foot 0..60] -> oil amount
    version: int = 1

    @property
    def length_ft(self) -> float:
        return LANE_LENGTH_FT

    @property
    def board_count(self) -> int:
        return BOARD_COUNT

    @staticmethod
    def house_shot() -> "LaneCondition":
        return LaneCondition._build(HOUSE_SHOT_SPEC)

    @staticmethod
    def _build(spec: OilPatternSpec) -> "LaneCondition":
        grid: list[tuple[float, ...]] = []
        for board in range(1, BOARD_COUNT + 1):
            lateral = _lateral_factor(board, spec)
            row = tuple(
                spec.peak_ml_per_ft * lateral * _longitudinal_factor(i * GRID_RESOLUTION_FT, spec)
                for i in range(GRID_LENGTH_CELLS)
            )
            grid.append(row)
        return LaneCondition(spec=spec, oil_grid=tuple(grid), version=1)

    def _cell_index(self, distance_ft: float, board: float) -> tuple[int, int]:
        board_idx = int(_clamp(round(board) - 1, 0, BOARD_COUNT - 1))
        dist_idx = int(_clamp(round(distance_ft / GRID_RESOLUTION_FT), 0, GRID_LENGTH_CELLS - 1))
        return board_idx, dist_idx

    def oil_at(self, distance_ft: float, board: float) -> float:
        board_idx, dist_idx = self._cell_index(distance_ft, board)
        return self.oil_grid[board_idx][dist_idx]

    def friction_at(self, distance_ft: float, board: float) -> float:
        """Friction coefficient at a point on the lane, derived from the oil
        remaining there. Always within [OILED_FRICTION, DRY_FRICTION],
        regardless of how far out of bounds distance/board are pushed.
        """
        peak = self.spec.peak_ml_per_ft
        if peak <= 0:
            return DRY_FRICTION

        oil_ratio = _clamp(self.oil_at(distance_ft, board) / peak, 0.0, 1.0)
        friction = DRY_FRICTION - oil_ratio * (DRY_FRICTION - OILED_FRICTION)
        return _clamp(friction, OILED_FRICTION, DRY_FRICTION)


def apply_wear(condition: LaneCondition, path) -> LaneCondition:
    """Pure function: a completed throw's path picks up oil along the boards
    it touched and carries a small amount of it further down those same
    boards. Returns a new, incremented LaneCondition — never mutates the one
    passed in, and oil amounts are clamped so they can never go negative.
    """
    grid = [list(row) for row in condition.oil_grid]
    touched: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    picked_up_total = 0.0

    for point in path:
        board_idx, dist_idx = condition._cell_index(point.distance_ft, point.board)
        key = (board_idx, dist_idx)
        if key in seen:
            continue
        seen.add(key)

        current = grid[board_idx][dist_idx]
        pickup = current * PICKUP_FRACTION
        grid[board_idx][dist_idx] = max(0.0, current - pickup)
        picked_up_total += pickup
        touched.append(key)

    if picked_up_total > 0 and touched:
        carry_per_cell = (picked_up_total * CARRYDOWN_FRACTION) / len(touched)
        offset_cells = int(CARRYDOWN_OFFSET_FT / GRID_RESOLUTION_FT)
        for board_idx, dist_idx in touched:
            target_idx = int(_clamp(dist_idx + offset_cells, 0, GRID_LENGTH_CELLS - 1))
            grid[board_idx][target_idx] = max(0.0, grid[board_idx][target_idx] + carry_per_cell)

    new_grid = tuple(tuple(row) for row in grid)
    return LaneCondition(spec=condition.spec, oil_grid=new_grid, version=condition.version + 1)
