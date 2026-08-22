"""Game-scoped lane sessions.

Lives outside `app.physics` on purpose: this is application/service-layer
state (which game owns which lane), not physics. A `GameSession` owns one
game's immutable starting `LaneCondition` and the mutable `LaneSession`
built from it; `GameService` owns the thread-safe mapping from an opaque
game ID to its `GameSession`. Nothing here does trajectory math — it exists
so `POST /api/v1/games/{id}/throws` can hand a request to the right lane,
and so two games' lanes never see each other's wear.
"""

import threading
import uuid
from typing import Dict, Optional

from app.physics.lane import LaneCondition
from app.physics.lane_session import LaneSession

# The only pattern selectable this milestone. A future named-pattern
# selection just adds more entries here (and to the request schema's
# Literal) — the route contract doesn't change.
SUPPORTED_OIL_PATTERNS = {"house": LaneCondition.house_shot}


class UnknownGameError(Exception):
    """Raised for an operation on a game_id the service doesn't hold. The
    API layer maps this to a 404."""


class GameSession:
    """One game's lane. `_initial_condition` is the exact condition this
    game started with — frozen and never mutated, so `reset` can hand it
    straight back out. (A `LaneCondition` is itself immutable, so reusing
    the same instance after wear has moved `lane` on to later versions is
    already "a fresh copy" in every observable sense — nothing could have
    mutated it in place.)
    """

    def __init__(self, game_id: str, initial_condition: LaneCondition):
        self.game_id = game_id
        self._initial_condition = initial_condition
        self.lane = LaneSession(initial_condition)

    def reset(self) -> LaneCondition:
        """Restore this game's lane to its own original starting condition
        — same grid, same temperature, version back to 1. Never touches the
        reusable OilPatternSpec/house_shot() factory itself."""
        self.lane.reset_to(self._initial_condition)
        return self._initial_condition


class GameService:
    """Thread-safe mapping from game_id to GameSession. The lock here only
    protects the mapping (create/lookup) — each GameSession's own
    LaneSession has its own lock around the read-simulate-record sequence
    for that game's throws.
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
# independent lane — this is the "hidden global lane" problem's fix, not a
# repeat of it: nothing simulates against this object directly, only
# against the one GameSession a request's game_id resolves to.
default_game_service = GameService()
