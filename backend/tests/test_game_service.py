"""Service-layer tests for game-scoped lane sessions — isolation, reset,
seed reproducibility across games, and concurrency within one game.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.games.service import GameService, UnknownGameError
from app.physics.ball import BALL_CATALOG
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
