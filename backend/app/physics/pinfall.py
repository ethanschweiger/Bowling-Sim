"""Pinfall resolution: turning an `ImpactState` into pins knocked down.

Sits behind `PinfallModel` so a future real collision solver — the
deterministic 2D pin-by-pin model described as a future architecture
constraint — can replace `EntryAngleHeuristicPinfallModel` without
changing anything upstream (impact construction, `impact.py`) or
downstream (frame scoring, once it exists) of it. Both implementations
would consume the same `ImpactState`; only what happens inside `resolve`
differs.

No pinfall model here may introduce randomness: resolving the same
`ImpactState` must always produce the same result. That already holds for
the heuristic below (it's a pure function of its input) and is a hard
requirement for whatever collision model replaces it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.physics.impact import ImpactState
from app.physics.pin_deck import LANE_CENTER_BOARD
from app.physics.units import BOARD_WIDTH_IN


@dataclass(frozen=True)
class PinfallResult:
    pins_knocked: int
    model_id: str
    limitations: str


class PinfallModel(ABC):
    """A pinfall model consumes an `ImpactState` and reports how many pins
    fell. A future collision model can extend this to also report *which*
    pin IDs fell (see pin_deck.py's Pin.id) — this milestone only commits
    to the count, which is what the API has always returned.
    """

    model_id: str

    @abstractmethod
    def resolve(self, impact: ImpactState) -> PinfallResult: ...


# The pocket (board 17.5, the 1-3 pocket for a right-handed bowler) and the
# lane edges (board 0 / board 40), re-expressed in the inches-from-center
# coordinate ImpactState uses, via the same declared board width the rest
# of the model reads from. Left-handers play the mirror (1-2 pocket);
# v1 still doesn't distinguish handedness — same limitation as before.
_POCKET_BOARD = 17.5
_POCKET_WINDOW_BOARDS = 1.5
_IDEAL_ENTRY_ANGLE_DEG = 5.0
_POCKET_LATERAL_IN = (_POCKET_BOARD - LANE_CENTER_BOARD) * BOARD_WIDTH_IN
_GUTTER_ABS_LATERAL_IN = LANE_CENTER_BOARD * BOARD_WIDTH_IN  # board 0 and board 40 are both this far from center


class EntryAngleHeuristicPinfallModel(PinfallModel):
    """A deterministic, simplified carry model — explicitly not a collision
    model. Dead center of the pocket at a good angle carries all ten; drift
    away from the pocket, or come in too square or too sharp, and pins
    stay up. Real carry depends on pin-to-pin action this doesn't model.

    This is the same rule the project has used since its first scoring
    pass, replumbed to consume `ImpactState` (inches from lane center)
    instead of reading a trajectory's board number directly.
    """

    model_id = "entry-angle-heuristic-v1"
    LIMITATIONS = (
        "Deterministic function of lateral position and heading only. No "
        "pin-to-pin interaction, no individual pin IDs, no restitution or "
        "mass involved in the calculation — not a collision model."
    )

    def resolve(self, impact: ImpactState) -> PinfallResult:
        return PinfallResult(
            pins_knocked=self._pins_knocked(impact),
            model_id=self.model_id,
            limitations=self.LIMITATIONS,
        )

    @staticmethod
    def _pins_knocked(impact: ImpactState) -> int:
        if abs(impact.lateral_position_in) >= _GUTTER_ABS_LATERAL_IN:
            return 0

        lateral_miss_boards = abs(impact.lateral_position_in - _POCKET_LATERAL_IN) / BOARD_WIDTH_IN
        angle_miss = abs(impact.heading_deg - _IDEAL_ENTRY_ANGLE_DEG)

        if lateral_miss_boards <= _POCKET_WINDOW_BOARDS and angle_miss <= 4.0:
            return 10

        # Carry falls off with distance from the pocket and from the ideal angle.
        miss_score = lateral_miss_boards * 1.1 + angle_miss * 0.6
        pins = round(10 - miss_score)
        return max(0, min(10, pins))


DEFAULT_PINFALL_MODEL: PinfallModel = EntryAngleHeuristicPinfallModel()
