"""`GameService`'s bounded-registry cap: FIFO eviction, self-preservation,
disabled/invalid caps, and that eviction never disturbs a retained game's
own lane/scorecard/rack isolation.

`test_game_service.py` covers the service's ordinary per-game behavior;
this file covers only what the cap adds. See `GameService`'s own
docstring in `app/games/service.py` for the policy these tests pin.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_game_service
from app.games.service import (
    DEFAULT_MAX_GAMES,
    GameService,
    InMemoryGameSessionRepository,
    UnknownGameError,
    default_game_service,
)
from app.main import app
from app.physics.ball import BALL_CATALOG
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw

client = TestClient(app)


def test_a_non_positive_cap_is_rejected_at_construction():
    """`0` or negative must fail loudly at construction -- never silently
    behave as unbounded, and never silently become "evict everything,
    including whatever a create call is about to return"."""
    with pytest.raises(ValueError):
        GameService(max_games=0)
    with pytest.raises(ValueError):
        GameService(max_games=-1)


def test_a_none_cap_disables_eviction_entirely():
    """The explicit, tested opt-out to the registry's original unbounded
    behavior -- not the production default, but a real supported mode."""
    service = GameService(max_games=None)

    created = [service.create_game() for _ in range(50)]

    assert len(service._repository._games) == 50
    for session in created:
        assert service.get_game(session.game_id) is session


def test_the_production_registry_is_bounded_by_default():
    """Wiring check: `default_game_service` actually receives
    `DEFAULT_MAX_GAMES`, not left at the unbounded original default.
    Asserted directly rather than by creating `DEFAULT_MAX_GAMES + 1`
    real games through the API, which would be slow and would not test
    anything this cheaper check doesn't already prove."""
    assert isinstance(DEFAULT_MAX_GAMES, int)
    assert DEFAULT_MAX_GAMES > 0
    assert default_game_service._repository._max_games == DEFAULT_MAX_GAMES


def test_oldest_game_is_evicted_when_creating_beyond_the_cap():
    service = GameService(max_games=2)
    a = service.create_game()
    b = service.create_game()

    c = service.create_game()

    assert list(service._repository._games) == [b.game_id, c.game_id]
    with pytest.raises(UnknownGameError):
        service.get_game(a.game_id)


def test_a_just_created_session_is_never_evicted_by_its_own_call():
    """The tightest possible case: cap of exactly 1. Every create evicts
    the previous game, but must never evict the one it is about to
    return -- eviction happens before insertion, not after."""
    service = GameService(max_games=1)

    a = service.create_game()
    assert service.get_game(a.game_id) is a

    b = service.create_game()
    assert service.get_game(b.game_id) is b
    with pytest.raises(UnknownGameError):
        service.get_game(a.game_id)

    c = service.create_game()
    assert service.get_game(c.game_id) is c
    with pytest.raises(UnknownGameError):
        service.get_game(b.game_id)


def test_get_or_create_for_an_existing_id_never_evicts():
    """A lookup hit is not a creation. It must return the same session
    and must not evict anything, no matter how tight the cap."""
    service = GameService(max_games=2)
    a = service.create_game()
    b = service.create_game()

    for _ in range(5):
        looked_up = service.get_or_create(b.game_id)
        assert looked_up is b

    # Neither game was touched by five repeated lookups of b.
    assert list(service._repository._games) == [a.game_id, b.game_id]
    assert service.get_game(a.game_id) is a


def test_get_or_create_creating_a_new_id_respects_the_cap():
    """The create-a-new-session branch of `get_or_create` is subject to
    the same eviction rule `create_game` is -- it is still a creation."""
    service = GameService(max_games=1)
    a = service.create_game()

    created = service.get_or_create("brand-new-id")

    assert created.game_id == "brand-new-id"
    assert list(service._repository._games) == ["brand-new-id"]
    with pytest.raises(UnknownGameError):
        service.get_game(a.game_id)


def test_retained_games_keep_independent_lane_and_score_state_after_a_sibling_is_evicted():
    """No regression to per-game isolation: evicting one game must not
    perturb another game's lane wear, scorecard, or rack in any way."""
    service = GameService(max_games=2)
    evicted_id = service.create_game().game_id
    b = service.create_game()
    b_condition_before = b.lane.condition

    # Evicts the first game; must not touch `b` at all.
    c = service.create_game()
    assert list(service._repository._games) == [b.game_id, c.game_id]
    with pytest.raises(UnknownGameError):
        service.get_game(evicted_id)

    ball = BALL_CATALOG["particle_beast"]
    for _ in range(3):
        c.lane.run_throw(lambda condition: simulate_throw(ball, Throw(), condition))

    # b is untouched by both the eviction and c's later throws.
    assert b.lane.condition == b_condition_before
    assert c.lane.condition.version == 4
    assert service.get_game(b.game_id) is b
    assert service.get_game(c.game_id) is c


def test_evicted_game_returns_404_through_the_api(monkeypatch):
    """The API-level shape of eviction: exactly the existing "unknown
    game_id" 404, with no new error shape or code path. Exercises a
    temporary, tightly capped `GameService` substituted through the
    get_game_service FastAPI dependency for this test only --
    `default_game_service` (used by the rest of the suite, at its real
    1000-game cap) is never touched. monkeypatch.setitem cleans the
    override up automatically at teardown."""
    temp_service = GameService(max_games=1)
    monkeypatch.setitem(app.dependency_overrides, get_game_service, lambda: temp_service)

    first = client.post("/api/v1/games", json={})
    assert first.status_code == 201
    first_id = first.json()["game_id"]

    second = client.post("/api/v1/games", json={})
    assert second.status_code == 201
    second_id = second.json()["game_id"]

    evicted = client.get(f"/api/v1/games/{first_id}")
    assert evicted.status_code == 404
    assert evicted.json()["detail"] == f"Unknown game_id '{first_id}'"

    retained = client.get(f"/api/v1/games/{second_id}")
    assert retained.status_code == 200
    assert retained.json()["game_id"] == second_id


def test_replacing_an_existing_entry_at_capacity_does_not_evict_a_sibling():
    """The write-back prerequisite fix: `put()` on a `game_id` this
    repository already holds is a replacement, not a creation, and must
    not evict another retained game just because the registry happens to
    be full at that moment -- see `InMemoryGameSessionRepository`'s own
    "The eviction policy" docstring for why this matters for
    `GameService.throw_in_game`/`reset_game`'s write-back calls."""
    repository = InMemoryGameSessionRepository(max_games=2)
    service = GameService(repository=repository)
    a = service.create_game()
    b = service.create_game()
    assert list(repository._games) == [a.game_id, b.game_id]

    # Replace b -- the registry is already at its cap of 2, but this must
    # not evict a, since b already existed (this is not a third game).
    repository.put(b)

    assert list(repository._games) == [a.game_id, b.game_id]
    assert service.get_game(a.game_id) is a
    assert service.get_game(b.game_id) is b


def test_replacing_an_existing_entry_does_not_change_its_retention_position():
    """A replacement must not act like a new insertion even when there is
    room to spare -- `game_id`'s position among retained games (oldest
    to newest) must be exactly what it already was, not moved to the
    end as if it had just been created."""
    repository = InMemoryGameSessionRepository(max_games=5)
    service = GameService(repository=repository)
    a = service.create_game()
    b = service.create_game()
    c = service.create_game()
    assert list(repository._games) == [a.game_id, b.game_id, c.game_id]

    # Replace the oldest entry, a -- its position must stay first, not
    # jump to the end the way a genuinely new game_id would land.
    repository.put(a)

    assert list(repository._games) == [a.game_id, b.game_id, c.game_id]
