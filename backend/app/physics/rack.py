"""Which physical pins are still standing — a small, immutable value object
over `pin_deck.STANDARD_DECK`'s ten pin IDs.

Intentionally independent of lane oil (`LaneCondition`, `lane.py`) and of
`Scorecard` (`app/scoring/scorecard.py`): a `Rack` only tracks *which*
pins remain between throws. It knows nothing about oil wear, and nothing
about frames or a running score. See "Ownership boundaries" in the README
for how a future `GameSession` is expected to hold a lane condition, a
scorecard, and a rack — three separate concerns, none of them each
other's business.

`Rack` is a genuinely immutable value: `validate_pin_ids` canonicalizes
whatever was passed in — a caller's own mutable `set` or `list` included
— into an owned `frozenset` before it's ever stored, so mutating the
collection a caller handed in (or trying to mutate `rack.standing_ids`
itself, which is always a real `frozenset` and so has no mutating methods
to call) can never change a `Rack` that already exists.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.physics.pin_deck import ALL_PIN_IDS


class RackError(Exception):
    """The one domain error for every invalid rack or pin-selection
    boundary in this module and in `collision.py`'s `standing_ids`
    handling. Raised for: a non-iterable input; any element that isn't an
    exact `int` (a `bool` or `float` that happens to equal a valid ID,
    e.g. `True == 1`, is rejected — it is not a harmless alternate
    spelling of that pin); an ID outside 1-10; a duplicate within the
    input; or (only from `Rack.after_fallen`) an ID that isn't currently
    standing. In every case, whatever rack or selection existed before
    the call is left completely unchanged — there is no way to observe a
    partially-applied update.
    """


def validate_pin_ids(ids: Iterable[int]) -> frozenset[int]:
    """Canonicalizes an arbitrary iterable of candidate pin IDs into an
    owned, validated `frozenset`. See `RackError` for exactly what's
    rejected. Used by `Rack` itself and by the planar collision model's
    `standing_ids` parameter, so both boundaries reject the same things
    the same way.
    """
    try:
        candidates = list(ids)
    except TypeError:
        raise RackError(f"pin IDs must come from an iterable, got {ids!r}") from None

    seen: set[int] = set()
    for pin_id in candidates:
        if type(pin_id) is not int:
            raise RackError(f"pin IDs must be plain int, got {pin_id!r} ({type(pin_id).__name__})")
        if pin_id not in ALL_PIN_IDS:
            raise RackError(f"{pin_id} is not a standard pin ID (1-10)")
        if pin_id in seen:
            raise RackError(f"duplicate pin ID: {pin_id}")
        seen.add(pin_id)
    return frozenset(seen)


@dataclass(frozen=True)
class Rack:
    standing_ids: frozenset[int]

    def __post_init__(self) -> None:
        # Canonicalize whatever was passed in — even a plain, currently-
        # mutable set or list — into an owned frozenset before storing it,
        # so this Rack can never be affected by later mutating the
        # caller's own collection. Frozen dataclasses need object.__setattr__
        # to set a field from inside __post_init__.
        validated = validate_pin_ids(self.standing_ids)
        object.__setattr__(self, "standing_ids", validated)

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
        completely unchanged — for anything `validate_pin_ids` rejects, or
        for an ID that isn't currently standing on this particular rack.
        """
        validated = validate_pin_ids(fallen_ids)
        not_standing = validated - self.standing_ids
        if not_standing:
            raise RackError(f"not standing on this rack: {sorted(not_standing)}")
        return Rack(standing_ids=self.standing_ids - validated)

    def __contains__(self, pin_id: int) -> bool:
        return pin_id in self.standing_ids

    def __len__(self) -> int:
        return len(self.standing_ids)
