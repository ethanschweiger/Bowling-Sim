"""Which physical pins are still standing — a small, immutable value object
over `pin_deck.STANDARD_DECK`'s ten pin IDs.

Intentionally independent of lane oil (`LaneCondition`, `lane.py`) and of
`Scorecard` (`app/scoring/scorecard.py`): a `Rack` only tracks *which*
pins remain between throws. It knows nothing about oil wear, and nothing
about frames or a running score. See "Ownership boundaries" in the README
for how a future `GameSession` is expected to hold one of each — a lane
condition, a scorecard, and a rack — as three separate, independently
mutable pieces of state, not one another's concern.
"""

from dataclasses import dataclass
from typing import FrozenSet, Iterable

from app.physics.pin_deck import ALL_PIN_IDS


class RackError(Exception):
    """Raised for any invalid rack construction or update. The rack that
    raised (or the prior rack, for `after_fallen`) is left unchanged —
    there is no way to observe a `Rack` in a partially-applied state."""


@dataclass(frozen=True)
class Rack:
    standing_ids: FrozenSet[int]

    def __post_init__(self) -> None:
        invalid = self.standing_ids - ALL_PIN_IDS
        if invalid:
            raise RackError(f"not standard pin IDs: {sorted(invalid)}")

    @staticmethod
    def full() -> "Rack":
        """A fresh rack: all ten pins standing."""
        return Rack(standing_ids=ALL_PIN_IDS)

    def reset(self) -> "Rack":
        """A fresh rack, independent of what `self` currently holds —
        equivalent to `Rack.full()`."""
        return Rack.full()

    def after_fallen(self, fallen_ids: Iterable[int]) -> "Rack":
        """Returns a new `Rack` with `fallen_ids` removed from those
        currently standing on `self`. Raises `RackError` — leaving `self`
        completely unchanged — if `fallen_ids` contains anything outside
        1-10, a duplicate, or an ID that isn't currently standing.
        """
        fallen_list = list(fallen_ids)
        seen: set = set()
        for pin_id in fallen_list:
            if pin_id not in ALL_PIN_IDS:
                raise RackError(f"{pin_id!r} is not a standard pin ID (1-10)")
            if pin_id in seen:
                raise RackError(f"duplicate fallen pin ID: {pin_id}")
            if pin_id not in self.standing_ids:
                raise RackError(f"pin {pin_id} is not standing on this rack")
            seen.add(pin_id)
        return Rack(standing_ids=self.standing_ids - seen)

    def __contains__(self, pin_id: int) -> bool:
        return pin_id in self.standing_ids

    def __len__(self) -> int:
        return len(self.standing_ids)
