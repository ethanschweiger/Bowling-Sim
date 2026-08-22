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
"""

import threading
from typing import Callable, Optional

from app.physics.lane import LaneCondition, apply_wear


class LaneSession:
    def __init__(self, condition: Optional[LaneCondition] = None):
        self._condition = condition or LaneCondition.house_shot()
        self._lock = threading.Lock()

    @property
    def condition(self) -> LaneCondition:
        with self._lock:
            return self._condition

    def run_throw(self, simulate: Callable[[LaneCondition], object]):
        """Atomically read the current condition, simulate against it, and
        record the resulting wear. `simulate(condition)` must return an
        object with a `.path` of TrajectoryPoints (a SimulationResult).
        Held under the lock end to end, so no other call can read the same
        condition before this one's wear is recorded.
        """
        with self._lock:
            condition = self._condition
            result = simulate(condition)
            self._condition = apply_wear(condition, result.path)
            return result

    def reset(self) -> None:
        with self._lock:
            self._condition = LaneCondition.house_shot()


# One shared lane for the process, for now — there's no concept of separate
# games or tables yet. The API route reads/writes through this.
default_session = LaneSession()
