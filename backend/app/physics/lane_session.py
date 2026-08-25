"""The one piece of mutable state in the physics layer.

`LaneCondition` and `apply_wear` are pure — they never mutate anything.
Something still has to own "what the lane looks like right now" between
throws. That's this class: an in-memory session holding the current
condition, updated after each throw. Swapping this for a database row (per
game) or a multiplayer session store (per lane, per table) later doesn't
touch the physics module at all.

`run_throw` is the whole point of the lock: FastAPI runs sync `def` routes
in a threadpool, so two requests can genuinely interleave. Without a lock,
two threads could both read version N, simulate against it, and both
write — one throw's wear would silently vanish and both responses would
claim the same lane_condition_version. `run_throw` holds the lock across
the read, the simulation, and the write, so each throw sees a lane no
other throw has touched yet, and the version it reports is exactly the one
it ran against.

This class has no notion of a "game" — it's the primitive one game (or,
before game scoping existed, the one shared process-global lane) is built
from. See `app.games.service` for the game-scoped owner of one `LaneSession`
per game, including reset back to that game's own starting condition.
"""

# Keeps `X | None` usable on this project's Python 3.9 floor — see
# app/physics/throw.py's module docstring for the full explanation.
from __future__ import annotations

import threading
from collections.abc import Callable

from app.physics.lane import LaneCondition, apply_wear
from app.physics.simulate import SimulationResult


class LaneSession:
    def __init__(self, condition: LaneCondition | None = None):
        self._condition = condition or LaneCondition.house_shot()
        self._lock = threading.Lock()

    @property
    def condition(self) -> LaneCondition:
        with self._lock:
            return self._condition

    def run_throw(self, simulate: Callable[[LaneCondition], SimulationResult]) -> SimulationResult:
        """Atomically read the current condition, simulate against it, and
        record the resulting wear. `simulate(condition)` must return a
        `SimulationResult` (its `.path` is what wears the lane in). Held
        under the lock end to end, so no other call can read the same
        condition before this one's wear is recorded.
        """
        with self._lock:
            condition = self._condition
            result = simulate(condition)
            self._condition = apply_wear(condition, result.path)
            return result

    def reset_to(self, condition: LaneCondition) -> None:
        """Replace the current condition outright — no wear applied. Used
        for a real reset (back to a specific starting condition, e.g. a
        game's own original), not for recording a throw."""
        with self._lock:
            self._condition = condition

    def reset(self) -> None:
        """Convenience: reset to a brand-new default house shot. Callers
        that need to restore a *specific* starting condition (a game's own,
        which might one day carry a non-default pattern or temperature)
        should use `reset_to` instead."""
        self.reset_to(LaneCondition.house_shot())
