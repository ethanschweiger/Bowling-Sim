"""Lane and oil pattern model.

The lane is 60 feet from foul line to headpin, 39 boards wide. Oil sits on
top of it as a grid: one amount per (board, foot-of-distance) cell, in
milliliters. Friction at a point is derived from how much oil remains in
that cell relative to the pattern's peak concentration — heavy oil means
low friction and skid, no oil means high friction and hook.

The grid is the lane's *condition*, not a fixed pattern: `apply_wear` reads
a completed throw's path and returns a new condition with oil picked up
along that path and a little carried down-lane, the way a real ball wears
in a pattern over a game. `OilPatternSpec` is the reusable, never-mutated
definition of a pattern's shape; `LaneCondition` is what that shape looks
like right now, after however many throws have crossed it.
"""

import math
from dataclasses import dataclass

BOARD_COUNT = 39
LANE_LENGTH_FT = 60.0
GRID_RESOLUTION_FT = 1.0
GRID_LENGTH_CELLS = int(LANE_LENGTH_FT / GRID_RESOLUTION_FT) + 1  # feet 0..60 inclusive

OILED_FRICTION = 0.015   # heavy oil — the ball skids
DRY_FRICTION = 0.080     # no oil — the ball grips and hooks

# Temperature's effect on friction is real (warmer lanes tend to break oil
# down and migrate it faster, effectively drying boards out sooner) but
# small next to what oil coverage does. Modeled as a documented, bounded
# multiplier on friction, not derived from thermodynamics: at most a 10%
# swing, symmetric around a 72F reference.
REFERENCE_TEMPERATURE_F = 72.0
TEMPERATURE_FRICTION_COEFF = 0.001   # fractional friction change per degree F from reference
MAX_TEMPERATURE_ADJUSTMENT = 0.10    # hard cap on that fractional change, either direction


def _temperature_friction_multiplier(temperature_f: float) -> float:
    raw = 1.0 + TEMPERATURE_FRICTION_COEFF * (temperature_f - REFERENCE_TEMPERATURE_F)
    return _clamp(raw, 1.0 - MAX_TEMPERATURE_ADJUSTMENT, 1.0 + MAX_TEMPERATURE_ADJUSTMENT)

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
    """The stated shape of a pattern: how far it runs, how it tapers, the
    lateral volume ratio between the boards it favors and the boards it
    barely touches, and the total oil laid down. This is a description, not
    a lane state — `LaneCondition` is what gets built from it and worn down
    over a game."""

    name: str
    length_ft: float                  # distance the pattern extends down the lane
    taper_ft: float                   # feet over which volume ramps to zero at the end
    center_boards: tuple[int, int]    # inclusive board range carrying full (peak) volume
    total_boards: tuple[int, int]     # inclusive board range carrying any oil at all
    pattern_ratio: float              # peak-board volume : edge-of-pattern volume
    total_volume_ml: float            # total oil laid down across the whole pattern


# A standard house shot: forgiving, oil concentrated in the center of the
# lane. Modeling assumptions, stated explicitly since this isn't a
# certified USBC pattern:
#   - 40 feet long with a 6-foot taper into the dry back end, in line with
#     a typical modern house pattern (most run roughly 32-40 ft).
#   - A 3:1 volume ratio between the peak (center) boards and the boards at
#     the pattern's outer edge.
#   - 22 mL total volume across the pattern, in line with a typical house
#     shot (most run in the high teens to mid-20s of mL).
# The grid built from this spec sums to exactly `total_volume_ml` — see
# LaneCondition._build.
HOUSE_SHOT_SPEC = OilPatternSpec(
    name="House Shot",
    length_ft=40.0,
    taper_ft=6.0,
    center_boards=(8, 32),
    total_boards=(3, 37),
    pattern_ratio=3.0,
    total_volume_ml=22.0,
)


def _lateral_factor(board: int, spec: OilPatternSpec) -> float:
    """0..1 shape multiplier for a board: 1.0 across the center boards,
    ramping down to 1/pattern_ratio at the pattern's outer edge, 0 past it.
    This is shape only — turning it into real mL happens once, in _build.
    """
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
    """0..1 shape multiplier along the lane's length: full volume until the
    taper starts, linearly down to zero at the pattern's stated length."""
    taper_start = max(0.0, spec.length_ft - spec.taper_ft)
    if distance_ft >= spec.length_ft:
        return 0.0
    if distance_ft <= taper_start:
        return 1.0
    return (spec.length_ft - distance_ft) / (spec.length_ft - taper_start)


@dataclass(frozen=True)
class LaneCondition:
    """A snapshot of oil on the lane, in mL per cell. Immutable — wear
    produces a new snapshot rather than mutating this one, so the physics
    layer stays pure.

    `peak_oil_ml` is the pattern's fresh peak concentration (center board,
    distance 0) — the fixed reference `friction_at` measures every cell
    against. It's carried forward unchanged by `apply_wear`: friction
    reflects how much of *this pattern's original* oil remains, not how
    much is left relative to whatever the current wettest cell happens to
    be.

    `temperature_f` is a stored lane parameter, carried forward unchanged
    by `apply_wear` (a throw doesn't change the room's temperature). See
    `_temperature_friction_multiplier` for its (small, bounded) effect on
    `friction_at`.
    """

    spec: OilPatternSpec
    oil_grid: tuple[tuple[float, ...], ...]  # [board 1..39][foot 0..60] -> mL
    peak_oil_ml: float
    temperature_f: float = REFERENCE_TEMPERATURE_F
    version: int = 1

    @property
    def length_ft(self) -> float:
        return LANE_LENGTH_FT

    @property
    def board_count(self) -> int:
        return BOARD_COUNT

    @staticmethod
    def house_shot(temperature_f: float = REFERENCE_TEMPERATURE_F) -> "LaneCondition":
        return LaneCondition._build(HOUSE_SHOT_SPEC, temperature_f=temperature_f)

    @staticmethod
    def _build(spec: OilPatternSpec, temperature_f: float = REFERENCE_TEMPERATURE_F) -> "LaneCondition":
        shape: list[list[float]] = []
        for board in range(1, BOARD_COUNT + 1):
            lateral = _lateral_factor(board, spec)
            shape.append(
                [lateral * _longitudinal_factor(i * GRID_RESOLUTION_FT, spec) for i in range(GRID_LENGTH_CELLS)]
            )

        shape_sum = sum(sum(row) for row in shape)
        # shape's max value is 1.0 (center board, distance 0), so once
        # scaled by unit_scale that cell holds exactly the pattern's peak
        # concentration — see peak_oil_ml below.
        unit_scale = spec.total_volume_ml / shape_sum if shape_sum > 0 else 0.0

        grid = tuple(tuple(v * unit_scale for v in row) for row in shape)
        return LaneCondition(spec=spec, oil_grid=grid, peak_oil_ml=unit_scale, temperature_f=temperature_f, version=1)

    def _cell_index(self, distance_ft: float, board: float) -> tuple[int, int]:
        board_idx = int(_clamp(round(board) - 1, 0, BOARD_COUNT - 1))
        dist_idx = int(_clamp(round(distance_ft / GRID_RESOLUTION_FT), 0, GRID_LENGTH_CELLS - 1))
        return board_idx, dist_idx

    def oil_at(self, distance_ft: float, board: float) -> float:
        """Oil in the single grid cell containing this point.

        Cell-resolution, deliberately: this is the quantity `apply_wear`
        adds to and subtracts from, and wear is bookkeeping over discrete
        cells. For reading a *force* along a trajectory, use
        `oil_at_interpolated`, which doesn't step at cell boundaries.
        """
        board_idx, dist_idx = self._cell_index(distance_ft, board)
        return self.oil_grid[board_idx][dist_idx]

    def oil_at_interpolated(self, distance_ft: float, board: float) -> float:
        """Oil at a point, bilinearly interpolated between the four
        surrounding grid cells.

        The grid stores one value per (board, foot). Reading it by nearest
        cell makes oil — and therefore friction, and therefore the lateral
        force — a staircase: constant for a foot, then a step. Integrating
        a trajectory through that produces small direction changes at cell
        boundaries that are artifacts of the lookup, not of the physics,
        and they show up as kinks in the drawn path. Interpolating makes
        the field continuous, so a smooth path stays smooth.

        The stored grid is unchanged; this only changes how it is sampled.
        Cell centres sit at integer boards and whole feet, so interpolating
        at a cell centre returns exactly that cell's value.
        """
        # Continuous cell coordinates: board N sits at index N-1, distance
        # D ft at index D / GRID_RESOLUTION_FT.
        board_pos = _clamp(board - 1.0, 0.0, BOARD_COUNT - 1)
        dist_pos = _clamp(distance_ft / GRID_RESOLUTION_FT, 0.0, GRID_LENGTH_CELLS - 1)

        b0 = int(math.floor(board_pos))
        d0 = int(math.floor(dist_pos))
        b1 = min(b0 + 1, BOARD_COUNT - 1)
        d1 = min(d0 + 1, GRID_LENGTH_CELLS - 1)
        bf = board_pos - b0
        df = dist_pos - d0

        top = self.oil_grid[b0][d0] * (1.0 - df) + self.oil_grid[b0][d1] * df
        bottom = self.oil_grid[b1][d0] * (1.0 - df) + self.oil_grid[b1][d1] * df
        return top * (1.0 - bf) + bottom * bf

    def friction_at(self, distance_ft: float, board: float) -> float:
        """Friction coefficient at a point on the lane, derived from the oil
        remaining there relative to the pattern's fresh peak, then adjusted
        by temperature. Always within [OILED_FRICTION, DRY_FRICTION],
        regardless of how far out of bounds distance/board/temperature are
        pushed.

        Samples the oil field continuously (`oil_at_interpolated`), so
        friction varies smoothly along a path instead of stepping at every
        grid boundary.
        """
        if self.peak_oil_ml <= 0:
            base = DRY_FRICTION
        else:
            oil_ratio = _clamp(self.oil_at_interpolated(distance_ft, board) / self.peak_oil_ml, 0.0, 1.0)
            base = DRY_FRICTION - oil_ratio * (DRY_FRICTION - OILED_FRICTION)

        adjusted = base * _temperature_friction_multiplier(self.temperature_f)
        return _clamp(adjusted, OILED_FRICTION, DRY_FRICTION)


def apply_wear(condition: LaneCondition, path) -> LaneCondition:
    """Pure function: a completed throw's path picks up oil along the boards
    it touched and carries a small amount of it further down those same
    boards. Returns a new, incremented LaneCondition — never mutates the one
    passed in, and oil amounts are clamped so they can never go negative.
    `peak_oil_ml` (the pattern's fresh reference) carries forward unchanged.
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
    return LaneCondition(
        spec=condition.spec,
        oil_grid=new_grid,
        peak_oil_ml=condition.peak_oil_ml,
        temperature_f=condition.temperature_f,
        version=condition.version + 1,
    )
