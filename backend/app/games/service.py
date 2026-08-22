"""Game-scoped lane sessions.

Lives outside `app.physics` on purpose: this is application/service-layer
state (which game owns which lane, scorecard, and rack), not physics. A
`GameSession` owns one game's immutable starting `LaneCondition` and the
mutable `LaneSession` built from it, one `Scorecard`, and one rack slot
holding an immutable `Rack`. `GameService` owns the thread-safe mapping
from an opaque game ID to its `GameSession`. Nothing here does trajectory
math or scoring math — it exists so `POST /api/v1/games/{id}/throws` can
hand a request to the right game's state, and so two games never see each
other's wear, pinfall, or score.

## Ownership boundaries

One `GameSession` owns exactly three independently mutable slots — a
`LaneCondition`/`LaneSession`, a `Scorecard`, and a `Rack` — and none of
the three value/rule objects is aware of the others or of `GameSession`
itself. `Rack` is immutable, the same way `LaneCondition` is: the rack
slot is a single reference `GameSession.throw`/`reset` atomically replaces
with the next `Rack` value, the same pattern `LaneSession` already uses
for `LaneCondition` (`reset_to`). The reusable, never-mutated
definitions — `OilPatternSpec`/`LaneCondition.house_shot()` and
`pin_deck.STANDARD_DECK` — stay shared across every game, exactly as
before. All three slots (lane, scorecard, rack) are in-memory and
per-game right now; a future move to shared storage or WebSocket-
synchronized multiplayer changes who holds a `GameSession` (or where its
state lives), not the collision solver, the scorecard rules, or the
rack's own logic — none of those know a game or a session exists.

## The throw transaction

`GameSession.throw` is the one place all three slots change together. It
holds a single per-game lock across the entire operation: checking the
game isn't already complete, reading the current rack, running the
trajectory simulation and pinfall resolution the caller supplies, wearing
the lane in, recording the resulting pinfall in the scorecard, and
replacing the rack. A throw against an already-complete game is rejected
before anything — lane, rack, scorecard, condition version — changes.
Two different games' sessions have entirely separate locks, so they never
block each other; within one game, this lock is what makes concurrent
throws against it serialize safely instead of racing.
"""

import threading
import uuid
from typing import Callable, Dict, Optional

from app.physics.lane import LaneCondition
from app.physics.lane_session import LaneSession
from app.physics.rack import Rack
from app.scoring.scorecard import Scorecard

# The only pattern selectable this milestone. A future named-pattern
# selection just adds more entries here (and to the request schema's
# Literal) — the route contract doesn't change.
SUPPORTED_OIL_PATTERNS = {"house": LaneCondition.house_shot}


class UnknownGameError(Exception):
    """Raised for an operation on a game_id the service doesn't hold. The
    API layer maps this to a 404."""


class GameCompleteError(Exception):
    """Raised by `GameSession.throw` when the game already has ten
    complete frames (plus any required tenth-frame bonus balls). The
    game's lane, rack, and scorecard are left completely unchanged — the
    rejection happens before any of the three is touched."""


class GameSession:
    """One game's lane, scorecard, and rack. `_initial_condition` is the
    exact lane condition this game started with — frozen and never
    mutated, so `reset` can hand it straight back out. (A `LaneCondition`
    is itself immutable, so reusing the same instance after wear has
    moved `lane` on to later versions is already "a fresh copy" in every
    observable sense — nothing could have mutated it in place.)
    """

    def __init__(self, game_id: str, initial_condition: LaneCondition):
        self.game_id = game_id
        self._initial_condition = initial_condition
        self.lane = LaneSession(initial_condition)
        self._scorecard = Scorecard()
        self._rack = Rack.full()
        self._lock = threading.Lock()

    @property
    def rack(self) -> Rack:
        """The pins currently standing. `Rack` is immutable, so handing
        out the live reference is safe — a caller can't mutate it even
        without holding the lock."""
        with self._lock:
            return self._rack

    @property
    def scorecard(self) -> Scorecard:
        """This game's live `Scorecard`. Read its `.frames`,
        `.total_score`, `.is_game_complete` etc. freely — only `throw()`
        ever calls `add_roll` on it, always under this session's lock."""
        with self._lock:
            return self._scorecard

    def snapshot(self):
        """Atomic read of (rack, scorecard) together — for building a
        `game_state` response, so a concurrent throw landing between two
        separate property reads can never produce a snapshot mixing an
        old rack with a newer scorecard or vice versa."""
        with self._lock:
            return self._rack, self._scorecard

    def throw(
        self,
        simulate: Callable[[LaneCondition], object],
        resolve_pinfall: Callable[[object, frozenset], object],
    ):
        """The one throw transaction. `simulate(lane_condition)` must
        return a `SimulationResult` (the same contract `LaneSession.run_throw`
        already uses); `resolve_pinfall(simulation_result, standing_ids)`
        must return a `PinfallResult` for that trajectory against exactly
        the supplied standing pin IDs. Neither callback should have side
        effects of its own — this method applies all of them: lane wear,
        the scorecard roll, and the rack transition, atomically under one
        lock.

        Raises `GameCompleteError` — before touching the lane, rack, or
        scorecard at all — if this game's tenth frame (and any required
        bonus balls) is already finished.

        Returns `(simulation_result, pinfall_result)`.
        """
        with self._lock:
            if self._scorecard.is_game_complete:
                raise GameCompleteError(f"game {self.game_id} is already complete")

            standing_ids = self._rack.standing_ids
            simulation_result = self.lane.run_throw(simulate)
            pinfall_result = resolve_pinfall(simulation_result, standing_ids)

            self._scorecard.add_roll(pinfall_result.pins_knocked)
            if self._scorecard.next_ball_starts_fresh_rack():
                self._rack = Rack.full()
            else:
                self._rack = self._rack.after_fallen(pinfall_result.fallen_pin_ids)

            return simulation_result, pinfall_result

    def reset(self) -> LaneCondition:
        """Restore this game's lane to its own original starting condition
        (same grid, same temperature, version back to 1), and start a
        fresh `Scorecard` and a full `Rack` — atomically, under the same
        lock `throw` uses. Never touches the reusable
        OilPatternSpec/house_shot() factory itself."""
        with self._lock:
            self.lane.reset_to(self._initial_condition)
            self._scorecard = Scorecard()
            self._rack = Rack.full()
            return self._initial_condition


class GameService:
    """Thread-safe mapping from game_id to GameSession. The lock here only
    protects the mapping (create/lookup) — each GameSession's own lock
    covers the read-simulate-record-update sequence for that game's
    throws (lane, scorecard, and rack together).
    """

    def __init__(self):
        self._games: Dict[str, GameSession] = {}
        self._lock = threading.Lock()

    def create_game(self, oil_pattern: str = "house", game_id: Optional[str] = None) -> GameSession:
        build_condition = SUPPORTED_OIL_PATTERNS.get(oil_pattern)
        if build_condition is None:
            raise ValueError(f"Unsupported oil_pattern '{oil_pattern}'")

        session = GameSession(game_id=game_id or uuid.uuid4().hex, initial_condition=build_condition())
        with self._lock:
            self._games[session.game_id] = session
        return session

    def get_or_create(self, game_id: str, oil_pattern: str = "house") -> GameSession:
        """Idempotent create: returns the existing session for `game_id` if
        one exists, otherwise creates it. Used by the legacy, non-game-scoped
        throw route to get one well-known, lazily-created game rather than
        owning a separate hidden lane of its own."""
        with self._lock:
            session = self._games.get(game_id)
            if session is not None:
                return session

            build_condition = SUPPORTED_OIL_PATTERNS.get(oil_pattern)
            if build_condition is None:
                raise ValueError(f"Unsupported oil_pattern '{oil_pattern}'")

            session = GameSession(game_id=game_id, initial_condition=build_condition())
            self._games[game_id] = session
            return session

    def get_game(self, game_id: str) -> GameSession:
        with self._lock:
            session = self._games.get(game_id)
        if session is None:
            raise UnknownGameError(game_id)
        return session


# One shared registry for the process. Each game inside it gets its own
# independent lane, scorecard, and rack — this is the "hidden global lane"
# problem's fix, not a repeat of it: nothing simulates against this object
# directly, only against the one GameSession a request's game_id resolves to.
default_game_service = GameService()
