"""Service-layer tests for game-scoped lane sessions — isolation, reset,
seed reproducibility across games, and concurrency within one game.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.games.service import (
    GameService,
    GameSession,
    InMemoryGameSessionRepository,
    UnknownGameError,
)
from app.physics.ball import BALL_CATALOG
from app.physics.lane import LaneCondition
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release


def test_two_games_begin_equal_and_wear_independently():
    service = GameService()
    game_a = service.create_game()
    game_b = service.create_game()

    assert game_a.lane.condition.oil_grid == game_b.lane.condition.oil_grid
    assert game_a.lane.condition.version == game_b.lane.condition.version == 1

    ball = BALL_CATALOG["particle_beast"]
    game_a.lane.run_throw(lambda condition: simulate_throw(ball, Throw(), condition))

    assert game_a.lane.condition.version == 2
    assert game_b.lane.condition.version == 1
    assert game_a.lane.condition.oil_grid != game_b.lane.condition.oil_grid


def test_a_throw_in_one_game_cannot_change_another_games_condition():
    service = GameService()
    game_a = service.create_game()
    game_b = service.create_game()
    game_b_condition_before = game_b.lane.condition

    ball = BALL_CATALOG["particle_beast"]
    for _ in range(5):
        game_a.lane.run_throw(lambda condition: simulate_throw(ball, Throw(), condition))

    assert game_b.lane.condition == game_b_condition_before


def test_reset_restores_original_grid_temperature_and_version():
    service = GameService()
    game = service.create_game()
    original = game.lane.condition

    ball = BALL_CATALOG["particle_beast"]
    for _ in range(5):
        game.lane.run_throw(lambda condition: simulate_throw(ball, Throw(), condition))
    assert game.lane.condition.version > 1
    assert game.lane.condition.oil_grid != original.oil_grid

    restored, snapshot = game.reset()
    assert restored.version == 1
    assert restored.oil_grid == original.oil_grid
    assert restored.temperature_f == original.temperature_f
    assert game.lane.condition == restored
    assert snapshot.lane_condition_version == 1


def test_unknown_game_id_raises():
    service = GameService()
    with pytest.raises(UnknownGameError):
        service.get_game("does-not-exist")


def test_same_seed_reproduces_trajectory_on_two_fresh_games():
    service = GameService()
    game_a = service.create_game()
    game_b = service.create_game()
    ball = BALL_CATALOG["reactive_pearl"]
    requested = Throw(
        speed_mph=17.0, rev_rate=350.0, axis_rotation=45.0,
        axis_tilt=15.0, launch_angle=0.5, launch_position=28.0,
    )

    sampled_a, seed_a = sample_release(requested, seed=123)
    sampled_b, seed_b = sample_release(requested, seed=123)
    assert seed_a == seed_b

    result_a = game_a.lane.run_throw(lambda condition: simulate_throw(ball, sampled_a, condition))
    result_b = game_b.lane.run_throw(lambda condition: simulate_throw(ball, sampled_b, condition))

    assert result_a.path == result_b.path
    assert result_a.entry_board == result_b.entry_board
    assert result_a.lane_condition_version == result_b.lane_condition_version == 1


def test_parallel_throws_in_one_game_preserve_sequential_versions():
    service = GameService()
    game = service.create_game()
    ball = BALL_CATALOG["house_ball"]
    throw = Throw()
    throw_count = 40

    def one_throw(_):
        return game.lane.run_throw(lambda condition: simulate_throw(ball, throw, condition))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one_throw, range(throw_count)))

    versions = sorted(result.lane_condition_version for result in results)
    assert versions == list(range(1, throw_count + 1))
    assert game.lane.condition.version == throw_count + 1


def test_get_or_create_for_an_existing_id_ignores_an_invalid_oil_pattern():
    """`get_or_create`'s oil_pattern validation only ever runs on the
    create-a-new-session path -- a lookup hit returns the existing game
    without even reading the argument. This was true of the original
    single-lock implementation (the existence check happened before
    oil_pattern was read at all) and stays true now that get_or_create
    delegates to a repository: the validating factory closure only runs
    if the repository actually calls it, which it doesn't for a hit."""
    service = GameService()
    existing = service.create_game()

    looked_up = service.get_or_create(existing.game_id, oil_pattern="not-a-real-pattern")

    assert looked_up is existing


def test_get_or_create_for_a_new_id_still_validates_the_oil_pattern():
    """The other half of the same contract: a genuinely new game_id does
    reach the factory, so an invalid oil_pattern still raises exactly as
    before -- this isn't validation silently disappearing, only deferred
    to when it's actually relevant."""
    service = GameService()

    with pytest.raises(ValueError):
        service.get_or_create("brand-new-id", oil_pattern="not-a-real-pattern")


class _CountingLock:
    """Wraps a real lock, counting how many times it was used as a
    context manager. Swapped in for `InMemoryGameSessionRepository`'s own
    `_lock` in the test below so "does `get_or_put` hold the lock across
    its whole check-then-store sequence" is a deterministic count rather
    than something a thread-pool stress test can only hope to observe --
    see that test's own docstring for why a stress test alone isn't
    trustworthy here."""

    def __init__(self, real_lock):
        self._real_lock = real_lock
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self._real_lock.__enter__()

    def __exit__(self, *args):
        return self._real_lock.__exit__(*args)


def test_get_or_put_holds_the_lock_for_its_entire_check_then_store_sequence():
    """The actual guarantee that prevents two concurrent get_or_create
    calls for the same game_id from creating two different sessions: the
    existence check and the conditional store must happen as a single
    critical section, not as two independent lock acquisitions (which is
    exactly what get()-then-put() as separate calls would be).

    Proven deterministically by counting lock acquisitions rather than by
    a thread-pool stress test -- CPython's GIL means a stress test for
    this exact race is not reliable: a from-scratch two-call
    get()-then-put() mutation of get_or_put was confirmed, by hand
    (removed before this commit), to still pass a 40-caller/16-thread
    stress test on every one of three separate runs, so a stress test
    alone would not have caught this class of regression.
    """
    repository = InMemoryGameSessionRepository()
    counting_lock = _CountingLock(repository._lock)
    repository._lock = counting_lock

    repository.get_or_put(
        "some-id",
        lambda: GameSession(game_id="some-id", initial_condition=LaneCondition.house_shot()),
    )

    assert counting_lock.enter_count == 1

    # A lookup hit must also stay a single critical section -- not "one
    # acquisition to check, a second to decide not to store."
    repository.get_or_put(
        "some-id", lambda: pytest.fail("factory must not run for an existing id")
    )

    assert counting_lock.enter_count == 2
