"""GameSession.throw(): the atomic lane+scorecard+rack transaction.

Uses a scripted `resolve_pinfall` callback (returning a chosen
`PinfallResult` regardless of the real trajectory) so every scorecard/rack
transition case can be driven deterministically, while still exercising
the real `simulate_throw` for lane wear and the real `GameSession.throw`
orchestration code end to end.
"""

import threading

import pytest

from app.games.service import GameCompleteError, GameService, GameStateSnapshot
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
            pins_knocked=pins_knocked,
            model_id="test-scripted",
            limitations="",
            fallen_pin_ids=tuple(fallen_pin_ids),
        )

    result, pinfall, _snapshot = session.throw(
        simulate=lambda condition: simulate_throw(BALL, THROW, condition),
        resolve_pinfall=resolve_pinfall,
    )
    return result, pinfall, seen["standing_ids"]


def test_throw_returns_a_subset_of_the_pre_throw_rack_and_narrows_it():
    session = GameService().create_game()
    pre_rack = session.current_snapshot().standing_pin_ids

    _, pinfall, standing_ids_used = _scripted_throw(session, 3, (1, 2, 3))
    assert standing_ids_used == pre_rack
    assert set(pinfall.fallen_pin_ids) <= pre_rack
    assert session.current_snapshot().standing_pin_ids == pre_rack - {1, 2, 3}


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

    assert game_b.current_snapshot().standing_pin_ids == ALL_PIN_IDS
    assert game_b.current_snapshot().frames == ()
    assert game_b.lane.condition.version == 1

    assert game_a.current_snapshot().standing_pin_ids == ALL_PIN_IDS - {1, 2, 3, 4, 5}
    assert game_a.current_snapshot().frames[0].rolls == (5,)
    assert game_a.lane.condition.version == 2


def test_ordinary_first_and_second_ball_rack_transitions():
    session = GameService().create_game()

    # Ball 1 of frame 1: ordinary, leaves the complement standing.
    _, _, standing_1 = _scripted_throw(session, 3, (1, 2, 3))
    assert standing_1 == ALL_PIN_IDS
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS - {1, 2, 3}

    # Ball 2 of frame 1: continues on that same complement (not fresh).
    _, _, standing_2 = _scripted_throw(session, 4, (4, 5, 6, 7))
    assert standing_2 == ALL_PIN_IDS - {1, 2, 3}
    frame1 = session.current_snapshot().frames[0]
    assert frame1.is_complete and frame1.rolls == (3, 4)

    # Frame 1 is complete (open frame, 3+4=7) — frame 2 ball 1 gets a fresh rack.
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS


def test_strike_resets_a_fresh_rack_for_the_next_frame():
    session = GameService().create_game()
    _, _, standing = _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    assert standing == ALL_PIN_IDS
    assert session.current_snapshot().frames[0].is_strike
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS  # fresh, not narrowed


def test_spare_resets_before_its_bonus_next_frame_ball():
    session = GameService().create_game()
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))
    _, _, standing_ball2 = _scripted_throw(session, 4, (7, 8, 9, 10))  # 6+4=10, spare
    assert standing_ball2 == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}
    assert session.current_snapshot().frames[0].is_spare
    # fresh for frame 2 / the bonus ball that scores it
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS


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
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS  # fresh for ball 2


def test_tenth_frame_second_bonus_strike_resets_before_ball_3():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # ball 1: strike
    _, _, standing_ball2 = _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # ball 2: also a strike
    assert standing_ball2 == ALL_PIN_IDS  # ball 2 ran against a fresh rack (ball 1 was a strike)
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS  # fresh again for ball 3


def test_tenth_frame_non_strike_second_bonus_ball_leaves_remainder_for_ball_3():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))  # ball 1: strike -> ball 2 fresh
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))  # ball 2: not a strike
    # NOT reset
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}

    _, _, standing_ball3 = _scripted_throw(session, 4, (7, 8, 9, 10))  # ball 3 clears what's left
    assert standing_ball3 == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}
    assert session.current_snapshot().is_game_complete


def test_tenth_frame_spare_resets_before_its_one_bonus_ball():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))  # ball 1: ordinary
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}

    _, _, standing_bonus = _scripted_throw(session, 4, (7, 8, 9, 10))  # ball 2: spare (6+4=10)
    assert standing_bonus == ALL_PIN_IDS - {1, 2, 3, 4, 5, 6}  # ball 2 itself ran on the remainder
    assert session.current_snapshot().frames[9].is_spare
    # fresh for the one bonus ball
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS


def test_completed_game_rejects_further_throws_without_changing_any_state():
    session = GameService().create_game()
    _run_to_frame_10(session)
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    _scripted_throw(session, 10, tuple(ALL_PIN_IDS))
    assert session.current_snapshot().is_game_complete

    lane_version_before = session.lane.condition.version
    standing_pins_before = session.current_snapshot().standing_pin_ids
    frames_before = session.current_snapshot().frames

    with pytest.raises(GameCompleteError):
        _scripted_throw(session, 5, (1, 2, 3, 4, 5))

    assert session.lane.condition.version == lane_version_before
    assert session.current_snapshot().standing_pin_ids == standing_pins_before
    assert session.current_snapshot().frames == frames_before


def test_reset_restores_full_rack_blank_scorecard_and_lane_version_1():
    session = GameService().create_game()
    _scripted_throw(session, 4, (1, 2, 3, 4))

    restored, snapshot = session.reset()

    assert restored.version == 1
    assert session.lane.condition.version == 1
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS
    assert session.current_snapshot().frames == ()
    assert snapshot.lane_condition_version == 1
    assert snapshot.standing_pin_ids == ALL_PIN_IDS
    assert snapshot.frames == ()
    assert snapshot.total_score is None
    assert not snapshot.is_game_complete
    assert session.current_snapshot().total_score is None
    assert not session.current_snapshot().is_game_complete


# --- Durable snapshots ------------------------------------------------


def _throw_and_capture_snapshot(session, pins_knocked, fallen_pin_ids):
    """Like _scripted_throw, but returns the actual GameStateSnapshot
    session.throw() produced, for tests that need to inspect it directly
    rather than re-deriving state from the live session afterward."""

    def resolve_pinfall(sim_result, standing_ids):
        return PinfallResult(
            pins_knocked=pins_knocked,
            model_id="test-scripted",
            limitations="",
            fallen_pin_ids=tuple(fallen_pin_ids),
        )

    return session.throw(
        simulate=lambda condition: simulate_throw(BALL, THROW, condition),
        resolve_pinfall=resolve_pinfall,
    )


def test_an_earlier_snapshot_is_unaffected_by_a_later_throw_or_reset():
    session = GameService().create_game()

    _, _, snapshot_1 = _throw_and_capture_snapshot(session, 3, (1, 2, 3))
    captured_frames = snapshot_1.frames
    captured_standing = snapshot_1.standing_pin_ids
    captured_lane_version = snapshot_1.lane_condition_version

    # A later throw, then a reset — both real, both mutate the live session.
    _throw_and_capture_snapshot(session, 4, (4, 5, 6, 7))
    session.reset()

    # The live session has clearly moved on...
    assert session.current_snapshot().frames == ()  # reset cleared it
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS

    # ...but the earlier snapshot's own fields are byte-identical to what
    # they were the moment it was captured.
    assert snapshot_1.frames == captured_frames
    assert snapshot_1.standing_pin_ids == captured_standing
    assert snapshot_1.lane_condition_version == captured_lane_version
    assert snapshot_1.standing_pin_ids == ALL_PIN_IDS - {1, 2, 3}
    assert snapshot_1.frames[0].rolls == (3,)


def test_a_throws_snapshot_is_immune_to_a_second_throw_completing_before_it_is_read():
    """Deterministic, using threading.Event — not sleeps. Proves the exact
    race the durable-snapshot design closes: throw 1 completes and hands
    back its snapshot; a second throw is allowed to fully commit on the
    same session *before* anything reads from throw 1's snapshot; the
    values read afterward must still describe throw 1, not throw 2.
    """
    session = GameService().create_game()

    _, _, snapshot_1 = _throw_and_capture_snapshot(session, 3, (1, 2, 3))

    second_throw_may_start = threading.Event()
    second_throw_done = threading.Event()

    def run_second_throw():
        second_throw_may_start.wait()
        _throw_and_capture_snapshot(session, 4, (4, 5, 6, 7))
        second_throw_done.set()

    worker = threading.Thread(target=run_second_throw)
    worker.start()

    # Release the second throw and wait — deterministically, via the
    # Event, not a sleep — until it has fully committed against the same
    # session, before reading anything from snapshot_1.
    second_throw_may_start.set()
    second_throw_done.wait()
    worker.join()

    # Proof the second throw really happened and really changed the live
    # session (otherwise this test would be vacuous):
    assert session.current_snapshot().frames[0].rolls == (3, 4)
    # frame 1 completed -> fresh rack
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS

    # snapshot_1, read only now — strictly after the second throw
    # committed — still describes exactly what throw 1 produced.
    assert snapshot_1.frames[0].rolls == (3,)
    assert snapshot_1.frames[0].is_complete is False
    assert snapshot_1.standing_pin_ids == ALL_PIN_IDS - {1, 2, 3}


# --- Encapsulation: GameStateSnapshot is the only read model -----------


def test_game_session_has_no_public_live_scorecard_or_rack_accessor():
    """Direct encapsulation regression. `GameSession` used to have public
    `.scorecard` and `.rack` properties, each handing out a live slot
    (mutable `Scorecard`, immutable `Rack`) after releasing the lock that
    protected the read — exactly the parallel inspection paths this
    module's docstring now says don't exist. Neither must exist:
    `current_snapshot()` and the `GameStateSnapshot` a throw()/reset()
    returns are the only public way to inspect a game's lane version,
    standing pins, frames, score, completion state, and next roll. This
    test never touches `.scorecard` or `.rack` themselves (it can't —
    asserting their absence is the point) and never holds a live
    reference of its own.
    """
    session = GameService().create_game()
    assert not hasattr(session, "scorecard")
    assert not hasattr(session, "rack")

    _, _, snapshot_1 = _throw_and_capture_snapshot(session, 3, (1, 2, 3))
    live_snapshot = session.current_snapshot()
    assert isinstance(snapshot_1, GameStateSnapshot)
    assert isinstance(live_snapshot, GameStateSnapshot)
    # the same standing-pin info .rack used to expose
    assert live_snapshot.standing_pin_ids == ALL_PIN_IDS - {1, 2, 3}
    captured_frames = live_snapshot.frames
    captured_total = live_snapshot.total_score
    captured_standing = live_snapshot.standing_pin_ids

    # Later throws and a reset mutate the live session...
    _throw_and_capture_snapshot(session, 4, (4, 5, 6, 7))
    session.reset()

    # ...but each earlier capture is a plain, already-immutable value, not
    # a reference into the session, so it still describes exactly what it
    # did the moment it was taken.
    assert live_snapshot.frames == captured_frames
    assert live_snapshot.total_score == captured_total
    assert live_snapshot.standing_pin_ids == captured_standing
    assert session.current_snapshot().frames == ()  # proves the session really did move on
    # reset really did return a fresh rack
    assert session.current_snapshot().standing_pin_ids == ALL_PIN_IDS
