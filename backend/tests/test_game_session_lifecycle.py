"""GameSession.throw(): the atomic lane+scorecard+rack transaction.

Uses a scripted `resolve_pinfall` callback (returning a chosen
`PinfallResult` regardless of the real trajectory) so every scorecard/rack
transition case can be driven deterministically, while still exercising
the real `simulate_throw` for lane wear and the real `GameSession.throw`
orchestration code end to end.
"""

import pytest

from app.games.service import GameCompleteError, GameService
from app.physics.ball import BALL_CATALOG
from app.physics.pin_deck import ALL_PIN_IDS
from app.physics.pinfall import PinfallResult
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw

BALL = BALL_CATALOG["house_ball"]
THROW = Throw()


def _scripted_throw(session, pins_knocked, fallen_pin_ids):
    """Runs a real trajectory (for real lane wear) but a scripted pinfall
    result, and returns (simulation_result, pinfall_result, standing_ids_seen)
    — the standing_ids the scripted resolve_pinfall was actually called
    with, so a test can assert exactly what rack this throw ran against.
    """
    seen = {}

    def resolve_pinfall(sim_result, standing_ids):
        seen["standing_ids"] = standing_ids
        return PinfallResult(
            pins_knocked=pins_knocked, model_id="test-scripted", limitations="", fallen_pin_ids=tuple(fallen_pin_ids)
        )

    result, pinfall = session.throw(simulate=lambda condition: simulate_throw(BALL, THROW, condition), resolve_pinfall=resolve_pinfall)
    return result, pinfall, seen["standing_ids"]


def test_throw_returns_a_subset_of_the_pre_throw_rack_and_narrows_it():
    session = GameService().create_game()
    pre_rack = session.rack.standing_ids

    _, pinfall, standing_ids_used = _scripted_throw(session, 3, (1, 2, 3))
    assert standing_ids_used == pre_rack
    assert set(pinfall.fallen_pin_ids) <= pre_rack
    assert session.rack.standing_ids == pre_rack - {1, 2, 3}


def test_next_throw_never_sees_a_pin_already_down():
    session = GameService().create_game()
    _scripted_throw(session, 3, (1, 2, 3))

    _, _, standing_ids_used = _scripted_throw(session, 0, ())
    assert standing_ids_used == ALL_PIN_IDS - {1, 2, 3}
    assert {1, 2, 3}.isdisjoint(standing_ids_used)


def test_two_games_have_isolated_rack_scorecard_and_lane_state():
    service = GameService()
    game_a = service.create_game()
    game_b = service.create_game()

    _scripted_throw(game_a, 5, (1, 2, 3, 4, 5))

    assert game_b.rack.standing_ids == ALL_PIN_IDS
    assert game_b.scorecard.frames == ()
    assert game_b.lane.condition.version == 1

    assert game_a.rack.standing_ids == ALL_PIN_IDS - {1, 2, 3, 4, 5}
    assert game_a.scorecard.frames[0].rolls == (5,)
    assert game_a.lane.condition.version == 2


def test_ordinary_first_and_second_ball_rack_transitions():
    session = GameService().create_game()

    # Ball 1 of frame 1: ordinary, leaves the complement standing.
    _, _, standing_1 = _scripted_throw(session, 3, (1, 2, 3))
    assert standing_1 == ALL_PIN_IDS
    assert session.rack.standing_ids == ALL_PIN_IDS - {1, 2, 3}

    # Ball 2 of frame 1: continues on that same complement (not fresh).
    _, _, standing_2 = _scripted_throw(session, 4, (4, 5, 6, 7))
    assert standing_2 == ALL_PIN_IDS - {1, 2, 3}
    frame1 = session.scorecard.frames[0]
    assert frame1.is_complete and frame1.rolls == (3, 4)

    # Frame 1 is complete (open frame, 3+4=7) — frame 2 ball 1 gets a fresh rack.
    assert session.rack.standing_ids == ALL_PIN_IDS


def test_strike_resets_a_fresh_rack_for_the_next_frame():
    session = GameService().create_game()
    _, _, standing = _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    assert standing == ALL_PIN_IDS
    assert session.scorecard.frames[0].is_strike
    assert session.rack.standing_ids == ALL_PIN_IDS  # fresh, not narrowed


def test_spare_resets_before_its_bonus_next_frame_ball():
    session = GameService().create_game()
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))
    _, _, standing_ball2 = _scripted_throw(session, 4, (7, 8, 9, 10))  # 6+4=10, spare
    assert standing_ball2 == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}
    assert session.scorecard.frames[0].is_spare
    assert session.rack.standing_ids == ALL_PIN_IDS  # fresh for frame 2 / the bonus ball that scores it


def _run_to_frame_10(session):
    """Frames 1-9 as strikes — the fastest legal path to frame 10, and it
    exercises "strike resets fresh" nine times over as a side effect."""
    for _ in range(9):
        _scripted_throw(session, 10, tuple(ALL_PIN_IDS))


def test_tenth_frame_first_ball_strike_resets_before_bonus_ball_2():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _, _, standing = _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # frame 10 ball 1: strike
    assert standing == ALL_PIN_IDS
    assert session.rack.standing_ids == ALL_PIN_IDS  # fresh for ball 2


def test_tenth_frame_second_bonus_strike_resets_before_ball_3():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # ball 1: strike
    _, _, standing_ball2 = _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # ball 2: also a strike
    assert standing_ball2 == ALL_PIN_IDS  # ball 2 ran against a fresh rack (ball 1 was a strike)
    assert session.rack.standing_ids == ALL_PIN_IDS  # fresh again for ball 3


def test_tenth_frame_non_strike_second_bonus_ball_leaves_remainder_for_ball_3():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # ball 1: strike -> ball 2 fresh
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))  # ball 2: not a strike
    assert session.rack.standing_ids == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}  # NOT reset

    _, _, standing_ball3 = _scripted_throw(session, 4, (7, 8, 9, 10))  # ball 3 clears what's left
    assert standing_ball3 == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}
    assert session.scorecard.is_game_complete


def test_tenth_frame_spare_resets_before_its_one_bonus_ball():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))  # ball 1: ordinary
    assert session.rack.standing_ids == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}

    _, _, standing_bonus = _scripted_throw(session, 4, (7, 8, 9, 10))  # ball 2: spare (6+4=10)
    assert standing_bonus == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}  # ball 2 itself ran on the remainder
    assert session.scorecard.frames[9].is_spare
    assert session.rack.standing_ids == ALL_PIN_IDS  # fresh for the one bonus ball


def test_completed_game_rejects_further_throws_without_changing_any_state():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    assert session.scorecard.is_game_complete

    lane_version_before = session.lane.condition.version
    rack_before = session.rack
    frames_before = session.scorecard.frames

    with pytest.raises(GameCompleteError):
        _scripted_throw(session, 5, (1, 2, 3, 4, 5))

    assert session.lane.condition.version == lane_version_before
    assert session.rack == rack_before
    assert session.scorecard.frames == frames_before


def test_reset_restores_full_rack_blank_scorecard_and_lane_version_1():
    session = GameService().create_game()
    _scripted_throw(session, 4, (1, 2, 3, 4))

    restored = session.reset()

    assert restored.version == 1
    assert session.lane.condition.version == 1
    assert session.rack.standing_ids == ALL_PIN_IDS
    assert session.scorecard.frames == ()
    assert session.scorecard.total_score is None
    assert not session.scorecard.is_game_complete
