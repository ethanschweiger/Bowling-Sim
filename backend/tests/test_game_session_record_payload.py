"""`app.games.record_payload`: the storage-safe conversion between a
`GameSessionRecord` and plain JSON-compatible primitives. Covers the
round trip (fresh, worn/partial-rack, and reset-after-wear games,
including a real `json.dumps`/`json.loads` pass, not just Python-object
equality) and every payload-shape failure the active task calls out by
name: missing required keys, malformed lane grid shape/type, illegal
stored rolls, and invalid standing pin IDs. See
`test_game_session_persistence.py` for `GameSession.to_record()`/
`from_record()` themselves, which this module's conversion sits in front
of and does not change.
"""

import json

import pytest

from app.games.record_payload import (
    GameSessionPayloadError,
    record_from_payload,
    record_to_payload,
)
from app.games.service import GameService, GameSession
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
    a real trajectory (for real lane wear) with a scripted pinfall
    result."""

    def resolve_pinfall(_sim_result, _standing_ids):
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


def _through_json(payload):
    """A real JSON round trip, not just a Python dict copy -- proves the
    payload is actually made of JSON-compatible primitives, not e.g. a
    tuple or frozenset that `json.dumps` would reject outright or
    silently coerce."""
    return json.loads(json.dumps(payload))


def _round_trip(session):
    """session -> record -> payload -> JSON -> payload -> record ->
    session, returning the final restored GameSession."""
    record = session.to_record()
    payload = record_to_payload(record)
    reloaded_payload = _through_json(payload)
    rebuilt_record = record_from_payload(reloaded_payload)
    return GameSession.from_record(rebuilt_record)


def test_round_trips_a_fresh_game():
    session = GameService().create_game()
    restored = _round_trip(session)

    assert restored.current_snapshot() == session.current_snapshot()
    assert restored.lane.condition == session.lane.condition


def test_round_trips_a_worn_partial_rack_game():
    session = GameService().create_game()
    _scripted_throw(session, 3, (1, 2, 3))
    _scripted_throw(session, 4, (4, 5, 6, 7))
    _scripted_throw(session, 2, (8, 9))

    restored = _round_trip(session)

    assert restored.current_snapshot() == session.current_snapshot()
    assert restored.lane.condition == session.lane.condition
    assert restored.lane.condition.version == 4
    assert restored.current_snapshot().standing_pin_ids == ALL_PIN_IDS - {8, 9}

    # And the restored game keeps playing, not starting fresh.
    _, _pinfall, _standing = _scripted_throw(restored, 1, (10,))
    assert restored.current_snapshot().frames[0].rolls == (3, 4)
    assert restored.current_snapshot().frames[1].rolls == (2, 1)


def test_round_trips_a_reset_after_wear_game():
    session = GameService().create_game()
    _scripted_throw(session, 5, (1, 2, 3, 4, 5))
    _scripted_throw(session, 3, (6, 7, 8))
    session.reset()
    _scripted_throw(session, 2, (1, 2))  # wear it again, after the reset, before capturing

    restored = _round_trip(session)

    assert restored.current_snapshot() == session.current_snapshot()
    assert restored.lane.condition.version == 2  # one throw's wear since the reset
    assert restored.current_snapshot().frames[0].rolls == (2,)

    # The restored session's own reset() must return to the *original*
    # initial_condition (version 1) -- proof the payload kept
    # initial_condition and current_condition distinct, not collapsed to
    # whichever one this test happened to capture first.
    restored_initial, snapshot = restored.reset()
    assert restored_initial.version == 1
    assert restored.lane.condition.version == 1
    assert snapshot.frames == ()


def test_payload_round_trips_through_real_json_byte_for_byte_as_data():
    session = GameService().create_game()
    _scripted_throw(session, 7, (1, 2, 3, 4, 5, 6, 7))

    payload = record_to_payload(session.to_record())
    reloaded = _through_json(payload)

    assert reloaded == payload  # nothing lost or coerced by a real JSON pass
    assert isinstance(json.dumps(payload), str)


def test_payload_preserves_lane_condition_details_exactly():
    session = GameService().create_game()
    _scripted_throw(session, 6, (1, 2, 3, 4, 5, 6))
    record = session.to_record()

    payload = record_to_payload(record)
    rebuilt = record_from_payload(_through_json(payload))

    for attr in ("initial_condition", "current_condition"):
        original = getattr(record, attr)
        restored = getattr(rebuilt, attr)
        assert restored.spec == original.spec
        assert restored.oil_grid == original.oil_grid
        assert restored.peak_oil_ml == original.peak_oil_ml
        assert restored.temperature_f == original.temperature_f
        assert restored.version == original.version

    assert rebuilt == record  # every field, not just the ones asserted above


@pytest.mark.parametrize(
    "missing_key",
    ["game_id", "initial_condition", "current_condition", "rolls", "standing_pin_ids"],
)
def test_from_payload_rejects_a_missing_required_key(missing_key):
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    del payload[missing_key]

    with pytest.raises(GameSessionPayloadError, match=missing_key):
        record_from_payload(payload)


def test_from_payload_rejects_an_oil_grid_with_the_wrong_number_of_rows():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["initial_condition"]["oil_grid"] = payload["initial_condition"]["oil_grid"][:-1]

    with pytest.raises(GameSessionPayloadError, match="oil_grid"):
        record_from_payload(payload)


def test_from_payload_rejects_an_oil_grid_row_with_the_wrong_length():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["initial_condition"]["oil_grid"][0] = payload["initial_condition"]["oil_grid"][0][:-1]

    with pytest.raises(GameSessionPayloadError, match="oil_grid"):
        record_from_payload(payload)


def test_from_payload_rejects_a_non_numeric_oil_grid_cell():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["initial_condition"]["oil_grid"][0][0] = "not-a-number"

    with pytest.raises(GameSessionPayloadError, match="oil_grid"):
        record_from_payload(payload)


def test_from_payload_rejects_a_non_int_roll():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["rolls"] = ["not-an-int"]

    with pytest.raises(GameSessionPayloadError, match="rolls"):
        record_from_payload(payload)


def test_from_payload_rejects_a_non_int_standing_pin_id():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["standing_pin_ids"] = ["not-an-int"]

    with pytest.raises(GameSessionPayloadError, match="standing_pin_ids"):
        record_from_payload(payload)


def test_from_payload_rejects_a_duplicate_standing_pin_id():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["standing_pin_ids"] = [1, 1, 2]

    with pytest.raises(GameSessionPayloadError, match="duplicate"):
        record_from_payload(payload)


def test_from_payload_rejects_a_non_str_game_id():
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())
    payload["game_id"] = 12345

    with pytest.raises(GameSessionPayloadError, match="game_id"):
        record_from_payload(payload)


def test_from_payload_accepts_out_of_range_values_structurally_but_from_record_still_rejects_them():
    """Range/legality rules (0-10 pins, a legal roll sequence, 1-10 pin
    IDs) are deliberately not this module's job -- see "Where validation
    lives" in record_payload's own docstring. record_from_payload only
    checks shape, so an out-of-range value survives it; GameSession.from_record
    still catches it downstream, through the exact same Scorecard/Rack
    validation a live game already goes through.
    """
    session = GameService().create_game()
    payload = record_to_payload(session.to_record())

    out_of_range_roll_payload = dict(payload)
    out_of_range_roll_payload["rolls"] = [11]  # 11 > MAX_PINS, but still a plain int
    record_with_bad_roll = record_from_payload(out_of_range_roll_payload)  # does not raise
    with pytest.raises(ScorecardError):
        GameSession.from_record(record_with_bad_roll)

    out_of_range_pin_payload = dict(payload)
    out_of_range_pin_payload["standing_pin_ids"] = [99]  # not 1-10, but still a plain int
    record_with_bad_pin = record_from_payload(out_of_range_pin_payload)  # does not raise
    with pytest.raises(RackError):
        GameSession.from_record(record_with_bad_pin)


def test_from_payload_rejects_a_non_mapping_top_level_payload():
    with pytest.raises(GameSessionPayloadError):
        record_from_payload("not-a-mapping")  # type: ignore[arg-type]
