"""`GameSession.to_record()` / `GameSession.from_record()`: the dump/
rehydrate boundary for one game's complete internal state — lane wear,
scorecard rolls, and the standing rack — independent of any repository
or storage format. See "Durable session records" in
`app.games.service`'s own module docstring for the full design.

Reuses `test_game_session_lifecycle.py`'s `_scripted_throw` pattern: a
real trajectory (for real lane wear) with a scripted pinfall result, so
scorecard/rack transitions are deterministic while still exercising the
real `GameSession.throw` orchestration end to end.
"""

import pytest

from app.games.service import GameService, GameSession, GameSessionRecord
from app.physics.ball import BALL_CATALOG
from app.physics.pin_deck import ALL_PIN_IDS
from app.physics.pinfall import PinfallResult
from app.physics.rack import RackError
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw
from app.scoring.scorecard import ScorecardError

BALL = BALL_CATALOG["house_ball"]
THROW = Throw()


def _scripted_throw(session, pins_knocked, fallen_pin_ids):
    """Same pattern as test_game_session_lifecycle.py's identical helper:
    runs a real trajectory (for real lane wear) but a scripted pinfall
    result, and returns (simulation_result, pinfall_result, standing_ids_seen)
    — the standing_ids the scripted resolve_pinfall was actually called
    with, so a test can assert exactly what rack this throw ran against.
    """
    seen = {}

    def resolve_pinfall(_sim_result, standing_ids):
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


def _play_a_few_rolls(session):
    """Three scripted rolls with real lane wear: a strike-free open frame,
    a partial rack, so lane version, scorecard rolls, and standing pins
    are all non-default when a record is captured."""
    _scripted_throw(session, 3, (1, 2, 3))
    _scripted_throw(session, 4, (4, 5, 6, 7))
    _scripted_throw(session, 2, (8, 9))


def test_to_record_captures_game_id_initial_condition_rolls_and_standing_pins():
    session = GameService().create_game()
    original_initial_condition = session.lane.condition  # version 1, before any wear

    _play_a_few_rolls(session)

    record = session.to_record()
    assert record.game_id == session.game_id
    assert record.initial_condition == original_initial_condition
    assert record.current_condition == session.lane.condition
    assert record.current_condition.version == 4  # version 1 plus three throws' wear
    assert record.rolls == (3, 4, 2)
    # Frame 1 (3, 4) completed, so its second ball's fallen pins (4-7) never
    # mattered to the rack: a completed frame always hands the next ball a
    # fresh, full rack regardless of what that completing ball knocked down.
    # Frame 2's ball 1 (pins 8, 9 falling) is the only throw still reflected
    # in the standing rack -- see _play_a_few_rolls's docstring-equivalent
    # trace in this module for why.
    assert record.standing_pin_ids == ALL_PIN_IDS - {8, 9}


def test_to_record_on_a_fresh_game_reflects_the_untouched_starting_state():
    session = GameService().create_game()
    record = session.to_record()

    assert record.initial_condition == record.current_condition
    assert record.current_condition.version == 1
    assert record.rolls == ()
    assert record.standing_pin_ids == ALL_PIN_IDS


def test_from_record_rebuilds_a_session_whose_snapshot_matches_the_original():
    session = GameService().create_game()
    _play_a_few_rolls(session)

    record = session.to_record()
    restored = GameSession.from_record(record)

    assert restored.game_id == session.game_id
    assert restored.current_snapshot() == session.current_snapshot()
    assert restored.lane.condition == session.lane.condition


def test_restored_session_continues_play_rather_than_starting_a_fresh_game():
    session = GameService().create_game()
    _play_a_few_rolls(session)
    record = session.to_record()
    restored = GameSession.from_record(record)

    pre_throw_standing = restored.current_snapshot().standing_pin_ids
    assert pre_throw_standing == record.standing_pin_ids

    _, pinfall, standing_ids_used = _scripted_throw(restored, 1, (10,))
    # The throw ran against the *restored* rack (7 pins, {1,2,3,4,5,6,7,10}
    # minus nothing yet), not a fresh full one -- proof this continued the
    # record's own game instead of silently starting a new one.
    assert standing_ids_used == pre_throw_standing
    # This roll completes frame 2 (2, 1) -- not a fresh frame's opening
    # ball -- so it's frame 2 that gained a second roll, not a new frame 3.
    assert restored.current_snapshot().frames[-1].rolls == (2, 1)
    assert restored.lane.condition.version == 5  # the record's version 4 plus one more throw


def test_restored_session_reset_returns_to_the_records_own_initial_condition():
    session = GameService().create_game()
    _play_a_few_rolls(session)
    record = session.to_record()
    restored = GameSession.from_record(record)

    restored_initial, snapshot = restored.reset()
    assert restored_initial == record.initial_condition
    assert restored.lane.condition == record.initial_condition
    assert restored.lane.condition.version == 1
    assert snapshot.standing_pin_ids == ALL_PIN_IDS
    assert snapshot.frames == ()


def test_from_record_rejects_an_illegal_stored_roll_sequence():
    session = GameService().create_game()
    valid_record = session.to_record()
    illegal_record = GameSessionRecord(
        game_id=valid_record.game_id,
        oil_pattern=valid_record.oil_pattern,
        initial_condition=valid_record.initial_condition,
        current_condition=valid_record.current_condition,
        rolls=(5, 6),  # frame 1: 5 + 6 exceeds 10 pins
        standing_pin_ids=valid_record.standing_pin_ids,
    )

    with pytest.raises(ScorecardError):
        GameSession.from_record(illegal_record)


def test_from_record_rejects_an_unsupported_stored_oil_pattern():
    """A stored `oil_pattern` that isn't a `SUPPORTED_OIL_PATTERNS` key
    (a corrupted or stale record -- create_game/get_or_create can never
    produce a live GameSession with one) must still be rejected here,
    not silently accepted only to fail later trying to serialize a
    GameStateResponse."""
    session = GameService().create_game()
    valid_record = session.to_record()
    illegal_record = GameSessionRecord(
        game_id=valid_record.game_id,
        oil_pattern="sport",  # not yet a supported pattern
        initial_condition=valid_record.initial_condition,
        current_condition=valid_record.current_condition,
        rolls=valid_record.rolls,
        standing_pin_ids=valid_record.standing_pin_ids,
    )

    with pytest.raises(ValueError, match="sport"):
        GameSession.from_record(illegal_record)


def test_from_record_rejects_invalid_stored_standing_pin_ids():
    session = GameService().create_game()
    valid_record = session.to_record()
    illegal_record = GameSessionRecord(
        game_id=valid_record.game_id,
        oil_pattern=valid_record.oil_pattern,
        initial_condition=valid_record.initial_condition,
        current_condition=valid_record.current_condition,
        rolls=valid_record.rolls,
        standing_pin_ids=frozenset({99}),  # not a standard pin ID (1-10)
    )

    with pytest.raises(RackError):
        GameSession.from_record(illegal_record)
