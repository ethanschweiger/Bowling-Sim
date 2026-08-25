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

## The repository boundary

`GameService` used to own its storage directly: a `dict[str, GameSession]`,
a lock, and the eviction policy, all as its own private attributes. That
storage is now behind `GameSessionRepository` — a small interface
(`get`, `put`, `get_or_put`) — with `InMemoryGameSessionRepository` as the
only implementation today. `GameService` holds a repository and delegates
every storage operation to it; it no longer knows *how* or *where*
sessions are kept, only that a repository can look one up and store one.

The point is what a future persistent store would need to change: a new
`GameSessionRepository` implementation, nothing upstream of it. `GameSession`,
`GameStateSnapshot`, every physics/scoring module, every API route, and the
frontend are all unaware this boundary exists — none of them ever talk to
a repository, only to `GameService`. This milestone adds the replacement
point only: games still live in memory, `InMemoryGameSessionRepository`
is still the only implementation, and process/container restart still
loses every game exactly as before. `GameService`'s own public methods
(`create_game`, `get_or_create`, `get_game`) and every observable behavior
they produce — including the bounded-registry eviction policy below — are
unchanged; see `GameSessionRepository`'s docstring for why `get_or_create`
needed a third repository method (`get_or_put`), not just `get`+`put`, to
preserve its original atomicity.

## Durable session records

`GameStateSnapshot` and `GameSessionRecord` solve different problems and
neither substitutes for the other. `GameStateSnapshot` is a read model —
what a caller sees *about* a game (lane version, standing pins, frames,
score) — deliberately missing what it would take to rebuild play, most
notably the original lane condition and the raw roll sequence a
`Scorecard` was built from. `GameSessionRecord` is the complement: not
something to show a caller, but everything `GameSession.from_record`
needs to reconstruct a live, playable `GameSession` — including the
*original* `LaneCondition` a `reset()` on the rebuilt session must return
to, which `GameStateSnapshot` never carries at all.

`to_record`/`from_record` are a pure in-process dump/rehydrate pair, not
a serializer: `GameSessionRecord` holds live `LaneCondition` and
`frozenset`/`tuple` values, not bytes or JSON, and nothing in this module
imports a database driver, a file format, or a web framework. Turning a
`GameSessionRecord` into bytes (for an actual persistent store) or back
is a future repository implementation's job, entirely outside this
module — the same boundary `GameSessionRepository` already draws around
*where* a `GameSession` lives, this draws around *what it would take* to
put one back together. `InMemoryGameSessionRepository` is still the only
repository, still loses every session on process/container restart, and
nothing here changes that; this only adds the piece a future durable
repository would need in order to turn stored bytes back into a working
`GameSession`.

`from_record` rebuilds a session by replaying its stored `rolls` through
`Scorecard.from_rolls` and its stored `standing_pin_ids` through `Rack`'s
own constructor — the exact same validation `add_roll` and
`validate_pin_ids` already apply to a live game, not a parallel, looser
check invented for rehydration. An invalid stored record raises
`ScorecardError` or `RackError`, the same exceptions a live game would
have raised making the same illegal moves.
"""

# Keeps `X | None` usable on this project's Python 3.9 floor — see
# app/physics/throw.py's module docstring for the full explanation.
from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from app.physics.impact import require_reached_pin_deck
from app.physics.lane import LaneCondition
from app.physics.lane_session import LaneSession
from app.physics.pinfall import PinfallResult
from app.physics.rack import Rack
from app.physics.simulate import SimulationResult
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


@dataclass(frozen=True)
class GameSessionRecord:
    """Everything `GameSession.from_record` needs to rebuild a live,
    playable `GameSession` later. See "Durable session records" in this
    module's own docstring for how this differs from `GameStateSnapshot`
    and why both `initial_condition` and `current_condition` are stored,
    not just one.
    """

    game_id: str
    # The condition this game started with — what a restored session's own
    # reset() must return to, exactly like a freshly created game's would.
    initial_condition: LaneCondition
    # The condition this game is on right now, worn in by however many
    # throws it has taken since initial_condition.
    current_condition: LaneCondition
    # The flat pinfall-per-roll sequence Scorecard.from_rolls replays.
    rolls: tuple[int, ...]
    standing_pin_ids: frozenset[int]


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
        simulate: Callable[[LaneCondition], SimulationResult],
        resolve_pinfall: Callable[[SimulationResult, frozenset[int]], PinfallResult],
    ) -> tuple[SimulationResult, PinfallResult, GameStateSnapshot]:
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

    def to_record(self) -> GameSessionRecord:
        """A durable `GameSessionRecord` capturing everything needed to
        rebuild this session later, via `from_record`. Captured under this
        session's own lock — the same one `throw`/`reset` hold — so
        `current_condition`, `rolls`, and `standing_pin_ids` all describe
        the same instant, never a torn read where one reflects a throw
        still in flight and another doesn't.
        """
        with self._lock:
            return GameSessionRecord(
                game_id=self.game_id,
                initial_condition=self._initial_condition,
                current_condition=self.lane.condition,
                rolls=self._scorecard.rolls,
                standing_pin_ids=self._rack.standing_ids,
            )

    @classmethod
    def from_record(cls, record: GameSessionRecord) -> GameSession:
        """Rebuilds a live `GameSession` from a `GameSessionRecord` — the
        inverse of `to_record`. The result is not a fresh game: its lane
        is worn in to `record.current_condition`, its scorecard resumes
        from `record.rolls`, and its rack reflects `record.standing_pin_ids`
        — the next `throw()` continues this game, not a new one. `reset()`
        on the result still returns to `record.initial_condition`, exactly
        like a freshly created game's own reset would.

        Raises `ScorecardError` for an illegal stored roll sequence (via
        `Scorecard.from_rolls`) or `RackError` for invalid stored standing
        pin IDs (via `Rack`'s own constructor) — the same exceptions a
        live game would have raised making the same illegal moves, before
        this method returns anything.
        """
        session = cls(game_id=record.game_id, initial_condition=record.initial_condition)
        scorecard = Scorecard.from_rolls(record.rolls)
        rack = Rack(standing_ids=record.standing_pin_ids)
        session.lane.reset_to(record.current_condition)
        session._scorecard = scorecard
        session._rack = rack
        return session


# The production default for `InMemoryGameSessionRepository.max_games` /
# `GameService.max_games`. Comfortably above anything the backend's own
# test suite creates against `default_game_service` in one run (38,
# measured directly by instrumenting a full `pytest` pass), while still
# bounding a long-running process instead of letting it grow without
# limit. Not derived from any measured request-volume or memory target --
# a chosen ceiling, the same way `HOUSE_SHOT_SPEC`'s numbers are chosen
# rather than measured.
DEFAULT_MAX_GAMES = 1000


class GameSessionRepository(ABC):
    """The storage boundary `GameService` reads and writes through --
    everything it needs from wherever `GameSession`s actually live.
    `InMemoryGameSessionRepository` below is the only implementation
    today; a future persistent store implements these same three methods
    without `GameService`, `GameSession`, any API route, or the frontend
    changing at all.

    Three methods, not two: `get`/`put` alone would let `GameService`
    check for an existing session and then store a new one as two
    separate calls, which is exactly the race a repository boundary must
    not introduce. Two concurrent callers racing to create the *same*
    game_id (`GameService.get_or_create`'s whole reason to exist) could
    both see nothing from `get`, both build a session, and the second
    `put` would silently overwrite the first's -- losing whatever the
    first session had already recorded. `get_or_put` is what the original
    single-lock implementation actually guaranteed: the existence check
    and the conditional store happen as one atomic operation with respect
    to other calls for the same game_id, exactly as it did before this
    boundary existed. `create_game` never had that race (it never checks
    for an existing ID first), so it uses plain `put`.
    """

    @abstractmethod
    def get(self, game_id: str) -> GameSession | None:
        """The session stored under `game_id`, or `None` if this
        repository doesn't hold one. Never raises for a missing ID --
        translating absence into `UnknownGameError` is `GameService`'s
        job, not the repository's."""
        ...

    @abstractmethod
    def put(self, session: GameSession) -> None:
        """Stores `session` under its own `game_id`, unconditionally --
        replacing whatever was already there under that ID, if anything.
        An implementation that bounds its own capacity (like the
        in-memory one below) applies its own eviction policy here,
        atomically with the insert."""
        ...

    @abstractmethod
    def get_or_put(self, game_id: str, factory: Callable[[], GameSession]) -> GameSession:
        """Returns the existing session for `game_id` if this repository
        holds one; otherwise calls `factory()` exactly once, stores the
        result (applying this repository's own eviction policy, the same
        as `put`), and returns it. The whole check-then-store sequence is
        atomic with respect to other calls for the same `game_id` -- see
        this class's own docstring for why that matters."""
        ...


class InMemoryGameSessionRepository(GameSessionRepository):
    """The only concrete `GameSessionRepository` today: a thread-safe
    dict, bounded to at most `max_games` retained sessions. The lock here
    protects the mapping itself (get/put/evict) -- each `GameSession`'s
    own lock still covers that one game's read-simulate-record-update
    sequence, entirely separately.

    ## The eviction policy

    When storing a new session would put the registry over `max_games`,
    the single oldest retained session -- the one that has been in
    `_games` the longest, by insertion order, never by how recently it
    was read or thrown in -- is evicted first. This is deliberately FIFO,
    not LRU: an LRU policy would need every read (`get`, a throw) to
    reorder state, which the behavior this class exists to preserve
    (`get_or_put`'s lookups, throws, reset, scorecard/rack isolation all
    behaving exactly as before for any ID that remains retained) doesn't
    ask for and would make harder to reason about. There are no TTLs,
    wall-clock timers, or background sweeps -- eviction is a synchronous
    side effect of `put`/`get_or_put` storing a genuinely new session,
    nothing else ever removes an entry.

    Eviction always happens *before* the new session is inserted, so the
    session a `put`/`get_or_put` call is about to return can never be the
    one evicted by that same call, for any `max_games >= 1`.

    An evicted game_id then behaves exactly like a game_id that was never
    stored: `get` returns `None`, which `GameService.get_game` turns into
    `UnknownGameError`, which the API layer already turns into the same
    404 an unknown ID always produced. There is no separate "evicted"
    state to track or expose.
    """

    def __init__(self, max_games: int | None = DEFAULT_MAX_GAMES):
        """`max_games=None` disables the cap entirely -- unbounded, the
        registry's original behavior before it had a cap at all. That is
        an explicit, tested opt-out (see
        `test_a_none_cap_disables_eviction_entirely`), not a silent
        default; `default_game_service` always passes a real integer.

        Any non-`None` `max_games` must be a positive integer. `0` or a
        negative value raises `ValueError` immediately, here at
        construction, rather than silently behaving as unbounded or as
        "evict everything including whatever was just stored" -- see
        `test_a_non_positive_cap_is_rejected_at_construction`.
        """
        if max_games is not None and max_games < 1:
            raise ValueError(f"max_games must be a positive integer or None, got {max_games!r}")
        self._games: dict[str, GameSession] = {}
        self._lock = threading.Lock()
        self._max_games = max_games

    def _evict_oldest_locked(self) -> None:
        """Caller must already hold `self._lock`. Evicts retained sessions
        oldest-first until storing one more would not exceed
        `self._max_games`. A `while` rather than a single `if`: `_games`
        only ever grows by exactly one per `put`/`get_or_put` call today,
        so one eviction always suffices in practice, but a `while` doesn't
        depend on that staying true to keep its own invariant (never leave
        the registry over cap)."""
        if self._max_games is None:
            return
        while len(self._games) >= self._max_games:
            oldest_id = next(iter(self._games))
            del self._games[oldest_id]

    def get(self, game_id: str) -> GameSession | None:
        with self._lock:
            return self._games.get(game_id)

    def put(self, session: GameSession) -> None:
        with self._lock:
            self._evict_oldest_locked()
            self._games[session.game_id] = session

    def get_or_put(self, game_id: str, factory: Callable[[], GameSession]) -> GameSession:
        with self._lock:
            existing = self._games.get(game_id)
            if existing is not None:
                return existing
            session = factory()
            self._evict_oldest_locked()
            self._games[game_id] = session
            return session


class GameService:
    """A thin orchestrator over a `GameSessionRepository`: decides which
    oil pattern builds which `GameSession` and its initial `LaneCondition`,
    and translates a missing lookup into `UnknownGameError`. All storage,
    locking, and eviction live in the repository this class holds -- see
    "The repository boundary" in this module's own docstring, and
    `GameSessionRepository` for the storage interface itself.
    """

    def __init__(
        self,
        max_games: int | None = DEFAULT_MAX_GAMES,
        repository: GameSessionRepository | None = None,
    ):
        """`repository` is the storage boundary this service delegates
        to -- omit it (the common case, including every existing caller
        and test) and an `InMemoryGameSessionRepository` is built from
        `max_games`, preserving exactly the constructor signature this
        class had before the boundary existed. Passing `repository`
        explicitly is for a future persistent-store implementation, or a
        test that wants to inject one directly; `max_games` is ignored
        when `repository` is supplied, since capacity is that repository's
        own concern.
        """
        self._repository = (
            repository if repository is not None else InMemoryGameSessionRepository(max_games)
        )

    def create_game(self, oil_pattern: str = "house", game_id: str | None = None) -> GameSession:
        build_condition = SUPPORTED_OIL_PATTERNS.get(oil_pattern)
        if build_condition is None:
            raise ValueError(f"Unsupported oil_pattern '{oil_pattern}'")

        session = GameSession(
            game_id=game_id or uuid.uuid4().hex, initial_condition=build_condition()
        )
        self._repository.put(session)
        return session

    def get_or_create(self, game_id: str, oil_pattern: str = "house") -> GameSession:
        """Idempotent create: returns the existing session for `game_id` if
        one exists, otherwise creates it. Used by the legacy, non-game-scoped
        throw route to get one well-known, lazily-created game rather than
        owning a separate hidden lane of its own.

        A lookup hit never evicts anything and never changes `game_id`'s
        own position in the insertion order -- only creating a genuinely
        new session can trigger eviction, the same as `create_game`.
        `oil_pattern` is validated only inside `factory`, so it's only
        even read when `get_or_put` actually needs to build a new session --
        an existing game_id with an unsupported `oil_pattern` argument
        still returns the existing session without raising, exactly as
        before this method delegated to a repository.
        """

        def factory() -> GameSession:
            build_condition = SUPPORTED_OIL_PATTERNS.get(oil_pattern)
            if build_condition is None:
                raise ValueError(f"Unsupported oil_pattern '{oil_pattern}'")
            return GameSession(game_id=game_id, initial_condition=build_condition())

        return self._repository.get_or_put(game_id, factory)

    def get_game(self, game_id: str) -> GameSession:
        session = self._repository.get(game_id)
        if session is None:
            raise UnknownGameError(game_id)
        return session


# One shared registry for the process. Each game inside it gets its own
# independent lane, scorecard, and rack — this is the "hidden global lane"
# problem's fix, not a repeat of it: nothing simulates against this object
# directly, only against the one GameSession a request's game_id resolves to.
# Bounded at `DEFAULT_MAX_GAMES` — see `InMemoryGameSessionRepository`'s
# docstring for the eviction policy this applies to a long-running process.
default_game_service = GameService(max_games=DEFAULT_MAX_GAMES)
