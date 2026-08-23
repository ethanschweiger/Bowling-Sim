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

## Durable snapshots

`Scorecard` is a genuinely mutable object — `add_roll` reassigns its
internal state on the *same* instance. Handing out a live reference to it
(the way an earlier version of this module's `snapshot()` did) is unsafe
once the lock that protected the read is released: a second, later throw
can reassign that same Scorecard's frames before a caller finishes reading
from the reference it was handed, making an *earlier* throw's response
describe *newer* game state. `Rack` doesn't have this specific problem
(each instance is genuinely immutable — a later throw replaces the slot
with a different object, never mutates the one already handed out) —
but `GameSession` doesn't expose either one publicly, and not only for
the race. `current_snapshot()` and the `GameStateSnapshot` each
`throw()`/`reset()` returns are meant to be the *one* public read model
for a game session's lane version, standing pins, scorecard frames,
score, completion state, and next roll — not one model for the pins and
a second, parallel accessor for everything else. `GameSession` had
public `scorecard` and `rack` properties in earlier versions of this
module (each returning the live slot under the lock, then releasing
it); both are gone now. `scorecard` really was the data race described
above; `rack` wasn't, but a second, narrower inspection path left
public undermined the same single-read-model contract and would only
get harder to remove once a future storage or multiplayer backend
depended on it. `_rack`, like `_scorecard`, is a private transaction
slot now — read only from inside `throw()`/`reset()`/`_build_snapshot()`,
all of which already hold `self._lock`.

`GameStateSnapshot` is the fix: a frozen dataclass built *inside* the
lock, holding only plain, already-immutable values (an int, a frozenset,
a tuple of already-immutable `Frame` objects, primitives) — never a
reference to `self._scorecard` or `self._rack` itself. `throw()` and
`reset()` build and return their post-commit snapshot as part of their
own result, from inside the same lock that did the mutation; nothing
calls back into a session afterward to "read the result." `current_snapshot()`
is the only other way to get one — its own dedicated lock acquisition,
for read-only callers (game creation, the `GET` endpoint) that aren't
mid-mutation.
"""

# Keeps `X | None` usable on this project's Python 3.9 floor — see
# app/physics/throw.py's module docstring for the full explanation.
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.physics.impact import require_reached_pin_deck
from app.physics.lane import LaneCondition
from app.physics.lane_session import LaneSession
from app.physics.rack import Rack
from app.scoring.scorecard import Frame, Scorecard

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


@dataclass(frozen=True)
class GameStateSnapshot:
    """A fully durable, immutable snapshot of one game's state at one
    instant. See "Durable snapshots" in the module docstring for why this
    exists — every field here is already a plain, copy-safe value; there
    is no live `Scorecard` or `Rack` reference anywhere in it, and no
    later throw or reset on the owning `GameSession` can change what an
    already-captured snapshot describes.
    """

    lane_condition_version: int
    standing_pin_ids: frozenset[int]
    # Frame is itself an immutable frozen dataclass — safe to hold indefinitely.
    frames: tuple[Frame, ...]
    total_score: int | None
    is_game_complete: bool
    next_frame_number: int | None
    next_ball_number: int | None


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

    def _build_snapshot(self) -> GameStateSnapshot:
        """Builds a `GameStateSnapshot` from current state. Caller must
        already hold `self._lock` — this does not acquire it."""
        next_frame_number, next_ball_number = self._scorecard.next_roll_position()
        return GameStateSnapshot(
            lane_condition_version=self.lane.condition.version,
            standing_pin_ids=self._rack.standing_ids,
            frames=self._scorecard.frames,
            total_score=self._scorecard.total_score,
            is_game_complete=self._scorecard.is_game_complete,
            next_frame_number=next_frame_number,
            next_ball_number=next_ball_number,
        )

    def current_snapshot(self) -> GameStateSnapshot:
        """A fresh, durable snapshot of this game's state right now. Safe
        to call anytime — acquires the lock itself. For read-only callers
        (game creation, `GET /api/v1/games/{id}`) that aren't in the
        middle of a `throw`/`reset` of their own."""
        with self._lock:
            return self._build_snapshot()

    def throw(
        self,
        simulate: Callable[[LaneCondition], object],
        resolve_pinfall: Callable[[object, frozenset], object],
    ) -> tuple[object, object, GameStateSnapshot]:
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

        Returns `(simulation_result, pinfall_result, snapshot)` — the
        snapshot is this throw's exact post-commit state, built before the
        lock is released, so it can never describe a later throw's result.
        """
        with self._lock:
            if self._scorecard.is_game_complete:
                raise GameCompleteError(f"game {self.game_id} is already complete")

            standing_ids = self._rack.standing_ids
            # The validity check runs *inside* the simulate step, not after
            # it. `LaneSession.run_throw` applies lane wear the moment the
            # simulation returns — before pinfall resolution — so a route
            # that never reached the pin deck has to be refused here or it
            # would already have worn the lane on its way to failing. From
            # this position the raise leaves lane, rack, and scorecard all
            # untouched, exactly like the game-complete rejection above.
            simulation_result = self.lane.run_throw(
                lambda condition: require_reached_pin_deck(simulate(condition))
            )
            pinfall_result = resolve_pinfall(simulation_result, standing_ids)

            self._scorecard.add_roll(pinfall_result.pins_knocked)
            if self._scorecard.next_ball_starts_fresh_rack():
                self._rack = Rack.full()
            else:
                self._rack = self._rack.after_fallen(pinfall_result.fallen_pin_ids)

            snapshot = self._build_snapshot()
            return simulation_result, pinfall_result, snapshot

    def reset(self) -> tuple[LaneCondition, GameStateSnapshot]:
        """Restore this game's lane to its own original starting condition
        (same grid, same temperature, version back to 1), and start a
        fresh `Scorecard` and a full `Rack` — atomically, under the same
        lock `throw` uses. Never touches the reusable
        OilPatternSpec/house_shot() factory itself.

        Returns `(initial_lane_condition, snapshot)` — the snapshot is
        this reset's exact post-commit state, built before the lock is
        released.
        """
        with self._lock:
            self.lane.reset_to(self._initial_condition)
            self._scorecard = Scorecard()
            self._rack = Rack.full()
            snapshot = self._build_snapshot()
            return self._initial_condition, snapshot


class GameService:
    """Thread-safe mapping from game_id to GameSession. The lock here only
    protects the mapping (create/lookup) — each GameSession's own lock
    covers the read-simulate-record-update sequence for that game's
    throws (lane, scorecard, and rack together).
    """

    def __init__(self):
        self._games: dict[str, GameSession] = {}
        self._lock = threading.Lock()

    def create_game(self, oil_pattern: str = "house", game_id: str | None = None) -> GameSession:
        build_condition = SUPPORTED_OIL_PATTERNS.get(oil_pattern)
        if build_condition is None:
            raise ValueError(f"Unsupported oil_pattern '{oil_pattern}'")

        session = GameSession(
            game_id=game_id or uuid.uuid4().hex, initial_condition=build_condition()
        )
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
