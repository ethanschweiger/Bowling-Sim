"""The one piece of mutable state in the physics layer.

`LaneCondition` and `apply_wear` are pure — they never mutate anything.
Something still has to own "what the lane looks like right now" between
throws. That's this class: an in-memory session holding the current
condition, updated after each throw. Swapping this for a database row (per
game) or a multiplayer session store (per lane, per table) later doesn't
touch the physics module at all.
"""

from typing import Optional

from app.physics.lane import LaneCondition, apply_wear


class LaneSession:
    def __init__(self, condition: Optional[LaneCondition] = None):
        self._condition = condition or LaneCondition.house_shot()

    @property
    def condition(self) -> LaneCondition:
        return self._condition

    def record_throw(self, path) -> LaneCondition:
        """Apply a completed throw's wear and adopt the result as current."""
        self._condition = apply_wear(self._condition, path)
        return self._condition

    def reset(self) -> None:
        self._condition = LaneCondition.house_shot()


# One shared lane for the process, for now — there's no concept of separate
# games or tables yet. The API route reads/writes through this.
default_session = LaneSession()
