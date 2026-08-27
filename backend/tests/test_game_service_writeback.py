"""`GameService.throw_in_game`/`reset_game`: the write-back boundary that
calls a repository's `put()` after a successful mutation, and only after
a successful one. See `app.games.service`'s own module docstring, "The
repository boundary", for why an in-memory repository never needed this
before (mutating an in-memory `GameSession` in place is already visible
to it) and why a future persistent one would.

Uses a small local spy repository -- not a mock -- wrapping a real
`InMemoryGameSessionRepository`, so every assertion here is against real
storage behavior, with `put()` calls additionally recorded for
inspection.
"""

import pytest

from app.games.service import (
    GameCompleteError,
    GameService,
    GameSession,
    GameSessionRepository,
    InMemoryGameSessionRepository,
)
from app.physics.ball import BALL_CATALOG
from app.physics.impact import TruncatedTrajectoryError
from app.physics.pinfall import PinfallResult
from app.physics.simulate import SimulationResult, TerminalState, TrajectoryPoint, simulate_throw
from app.physics.throw import Throw

BALL = BALL_CATALOG["house_ball"]
THROW = Throw()


class _SpyRepository(GameSessionRepository):
    """Wraps a real `GameSessionRepository`, delegating every call to it,
    while recording each `game_id` a `put()` call is made for -- so a
    test can assert exactly when (and how many times) write-back
    happened, against real storage underneath."""

    def __init__(self, inner: GameSessionRepository):
        self._inner = inner
        self.put_calls: list[str] = []

    def get(self, game_id):
        return self._inner.get(game_id)

    def put(self, session):
        self.put_calls.append(session.game_id)
        self._inner.put(session)

    def get_or_put(self, game_id, factory):
        return self._inner.get_or_put(game_id, factory)


def _resolve_pinfall(pins_knocked, fallen_pin_ids):
    def resolve(_sim_result, _standing_ids):
        return PinfallResult(
            pins_knocked=pins_knocked,
            model_id="test-scripted",
            limitations="",
            fallen_pin_ids=tuple(fallen_pin_ids),
        )

    return resolve


def _real_simulate(condition):
    return simulate_throw(BALL, THROW, condition)


def _truncated_simulate(condition):
    """A `SimulationResult`-shaped stand-in whose result never reaches
    the pin deck -- deterministic, same pattern as
    test_games_api.py's `_truncated_simulate_throw`, but matching
    `GameSession.throw`'s own `simulate` signature directly (just
    `condition`, not `(ball, throw, condition, step_ft)`)."""
    return SimulationResult(
        path=[TrajectoryPoint(distance_ft=0.0, board=20.0)],
        entry_board=20.0,
        entry_angle_deg=0.0,
        speed_at_pins_mph=10.0,
        lane_condition_version=condition.version,
        terminal=TerminalState(
            distance_ft=10.0,
            board=20.0,
            heading_deg=0.0,
            speed_mph=10.0,
            reached_pin_deck=False,
        ),
    )


def _complete_a_game_directly(session):
    """Completes a game with 20 direct `session.throw()` calls, bypassing
    `GameService` entirely -- so this setup itself never touches the spy
    repository's `put_calls`, keeping it clean for the test's own
    assertion."""
    resolve = _resolve_pinfall(0, ())
    for _ in range(20):
        session.throw(simulate=_real_simulate, resolve_pinfall=resolve)


def _spy_service():
    spy = _SpyRepository(InMemoryGameSessionRepository())
    return GameService(repository=spy), spy


def test_throw_in_game_writes_the_mutated_session_back_after_success():
    service, spy = _spy_service()
    session = service.create_game()
    spy.put_calls.clear()  # only interested in the throw's own write-back

    service.throw_in_game(
        session, simulate=_real_simulate, resolve_pinfall=_resolve_pinfall(3, (1, 2, 3))
    )

    assert spy.put_calls == [session.game_id]


def test_throw_in_game_returns_exactly_what_session_throw_returns():
    service, _spy = _spy_service()
    session = service.create_game()

    direct_session = GameSession(
        game_id="reference", initial_condition=session.lane.condition, oil_pattern="house"
    )
    # Same scripted inputs against an independent, identically-conditioned
    # session, called directly (not through the service) -- proves
    # throw_in_game doesn't alter the result it hands back.
    expected = direct_session.throw(
        simulate=_real_simulate, resolve_pinfall=_resolve_pinfall(3, (1, 2, 3))
    )
    actual = service.throw_in_game(
        session, simulate=_real_simulate, resolve_pinfall=_resolve_pinfall(3, (1, 2, 3))
    )

    assert actual[0] == expected[0]  # SimulationResult
    assert actual[1] == expected[1]  # PinfallResult
    assert actual[2] == expected[2]  # GameStateSnapshot


def test_reset_game_writes_the_mutated_session_back_after_success():
    service, spy = _spy_service()
    session = service.create_game()
    service.throw_in_game(
        session, simulate=_real_simulate, resolve_pinfall=_resolve_pinfall(3, (1, 2, 3))
    )
    spy.put_calls.clear()  # only interested in the reset's own write-back

    service.reset_game(session)

    assert spy.put_calls == [session.game_id]


def test_throw_in_game_does_not_write_back_when_the_game_is_already_complete():
    service, spy = _spy_service()
    session = service.create_game()
    _complete_a_game_directly(session)
    spy.put_calls.clear()

    with pytest.raises(GameCompleteError):
        service.throw_in_game(
            session, simulate=_real_simulate, resolve_pinfall=_resolve_pinfall(0, ())
        )

    assert spy.put_calls == []


def test_throw_in_game_does_not_write_back_on_a_truncated_trajectory():
    service, spy = _spy_service()
    session = service.create_game()
    spy.put_calls.clear()

    def resolve_pinfall_must_not_run(_sim_result, _standing_ids):
        pytest.fail("resolve_pinfall must not run for a truncated trajectory")

    with pytest.raises(TruncatedTrajectoryError):
        service.throw_in_game(
            session, simulate=_truncated_simulate, resolve_pinfall=resolve_pinfall_must_not_run
        )

    assert spy.put_calls == []
