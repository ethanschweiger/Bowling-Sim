"""Pinfall resolution: turning an `ImpactState` into pins knocked down.

Sits behind `PinfallModel` so a collision solver can replace one
implementation with another without changing anything upstream (impact
construction, `impact.py`) or downstream (frame scoring, once it exists)
of it. Every implementation consumes the same `ImpactState`; only what
happens inside `resolve` differs.

No pinfall model here may introduce randomness: resolving the same
`ImpactState` must always produce the same result. `EntryAngleHeuristicPinfallModel`
below is a pure function of its input; `PlanarCollisionPinfallModel`
(`collision.py`) is the same, just by simulating physics instead of
applying a formula.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass

from app.physics.impact import ImpactState
from app.physics.pin_deck import GUTTER_ABS_LATERAL_IN, LANE_CENTER_BOARD
from app.physics.units import BOARD_WIDTH_IN


@dataclass(frozen=True)
class PinfallResult:
    pins_knocked: int
    model_id: str
    limitations: str
    # Empty for a model that can't identify individual pins (see
    # EntryAngleHeuristicPinfallModel's limitations) — not every model can
    # populate this, so it isn't guaranteed to satisfy
    # pins_knocked == len(fallen_pin_ids) across every implementation, only
    # ones that actually resolve individual pins.
    fallen_pin_ids: tuple = ()


class PinfallModel(ABC):
    """A pinfall model consumes an `ImpactState` and reports how many (and,
    where it can, which) pins fell.

    `standing_ids` is optional: omit it (or pass None) and a model treats
    every pin as standing — the same behavior as before this parameter
    existed, byte-for-byte. Pass a specific set (e.g. a `Rack.standing_ids`)
    and a model that can honor it — `PlanarCollisionPinfallModel` — only
    materializes and resolves those pins; any `fallen_pin_ids` it returns
    is guaranteed to be a subset of what was supplied. That model routes
    a supplied selection through `app.physics.rack.validate_pin_ids`,
    which raises `RackError` for anything malformed, unknown, or
    duplicated — the same check `Rack` itself applies. A model that can't
    honor per-pin selection (the heuristic) accepts the parameter for
    interface consistency but ignores it — see its own limitations.
    """

    model_id: str

    @abstractmethod
    def resolve(
        self, impact: ImpactState, *, standing_ids: Iterable[int] | None = None
    ) -> PinfallResult: ...


# The pocket (board 17.5, the 1-3 pocket for a right-handed bowler),
# re-expressed in the inches-from-center coordinate ImpactState uses, via
# the same declared board width the rest of the model reads from.
# Left-handers play the mirror (1-2 pocket); v1 still doesn't distinguish
# handedness — same limitation as before.
_POCKET_BOARD = 17.5
_POCKET_WINDOW_BOARDS = 1.5
_IDEAL_ENTRY_ANGLE_DEG = 5.0
_POCKET_LATERAL_IN = (_POCKET_BOARD - LANE_CENTER_BOARD) * BOARD_WIDTH_IN


class EntryAngleHeuristicPinfallModel(PinfallModel):
    """A deterministic, simplified carry model — explicitly not a collision
    model. Dead center of the pocket at a good angle carries all ten; drift
    away from the pocket, or come in too square or too sharp, and pins
    stay up. Real carry depends on pin-to-pin action this doesn't model.

    Kept as an explicitly labeled fallback for comparison and tests —
    `PlanarCollisionPinfallModel` (`collision.py`) is the API's default.
    """

    model_id = "entry-angle-heuristic-v1"
    LIMITATIONS = (
        "Deterministic function of lateral position and heading only. No "
        "pin-to-pin interaction, no restitution or mass involved in the "
        "calculation — not a collision model. Cannot identify individual "
        "pins: fallen_pin_ids is always empty, even when pins_knocked > 0. "
        "Accepts standing_ids for interface consistency but ignores it — "
        "the formula has no notion of which specific pins remain."
    )

    def resolve(
        self, impact: ImpactState, *, standing_ids: Iterable[int] | None = None
    ) -> PinfallResult:
        return PinfallResult(
            pins_knocked=self._pins_knocked(impact),
            model_id=self.model_id,
            limitations=self.LIMITATIONS,
            fallen_pin_ids=(),
        )

    @staticmethod
    def _pins_knocked(impact: ImpactState) -> int:
        if abs(impact.lateral_position_in) >= GUTTER_ABS_LATERAL_IN:
            return 0

        lateral_miss_boards = abs(impact.lateral_position_in - _POCKET_LATERAL_IN) / BOARD_WIDTH_IN
        angle_miss = abs(impact.heading_deg - _IDEAL_ENTRY_ANGLE_DEG)

        if lateral_miss_boards <= _POCKET_WINDOW_BOARDS and angle_miss <= 4.0:
            return 10

        # Carry falls off with distance from the pocket and from the ideal angle.
        miss_score = lateral_miss_boards * 1.1 + angle_miss * 0.6
        pins = round(10 - miss_score)
        return max(0, min(10, pins))
