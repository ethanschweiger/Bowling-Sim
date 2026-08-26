"""The nullable-int contract on the API's Pydantic models.

`app/models/schemas.py` cannot spell these fields as `X | None` without
breaking Pydantic on this project's Python 3.9 floor (Pydantic resolves
annotations eagerly regardless of `from __future__ import annotations`),
so it spells them as literal `Optional[int]` with a line-level `noqa` on
Ruff's UP045 (target py311 prefers `X | None`) -- `NullableInt` is that
alias. This file is the regression proving `NullableInt` behaves exactly
like `Optional[int]` in every way a caller of the API can observe: `None`
validates, serializes as JSON `null`, and the generated schema still
advertises the field as nullable. Runs unmodified under whichever Python
this suite is invoked with; nothing here is version-conditional.
"""

from app.models.schemas import FrameStateResponse, GameStateResponse, ThrowRequest

NULLABLE_JSON_SCHEMA = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def _is_nullable_int_schema(field_schema: dict) -> bool:
    """True if a JSON-schema property allows exactly integer-or-null —
    Pydantic v2's shape for `Optional[int]`, regardless of how the Python
    annotation that produced it was spelled."""
    any_of = field_schema.get("anyOf")
    if any_of is None:
        return False
    types = {entry.get("type") for entry in any_of}
    return types == {"integer", "null"}


def test_throw_request_seed_accepts_and_serializes_none():
    request = ThrowRequest(ball_id="reactive_pearl", seed=None)
    assert request.seed is None
    assert request.model_dump()["seed"] is None
    assert '"seed":null' in request.model_dump_json()

    schema = ThrowRequest.model_json_schema()
    assert _is_nullable_int_schema(schema["properties"]["seed"])


def test_frame_state_response_score_accepts_and_serializes_none():
    frame = FrameStateResponse(
        number=1,
        rolls=[10],
        is_strike=True,
        is_spare=False,
        is_complete=True,
        score=None,
        roll_symbols=["X"],
    )
    assert frame.score is None
    assert '"score":null' in frame.model_dump_json()

    schema = FrameStateResponse.model_json_schema()
    assert _is_nullable_int_schema(schema["properties"]["score"])


def test_game_state_response_nullable_fields_accept_and_serialize_none():
    unresolved_frame = FrameStateResponse(
        number=1,
        rolls=[3],
        is_strike=False,
        is_spare=False,
        is_complete=False,
        score=None,
        roll_symbols=["3"],
    )
    state = GameStateResponse(
        standing_pin_ids=[1, 2, 3],
        frames=[unresolved_frame],
        total_score=None,
        is_game_complete=False,
        next_frame_number=None,
        next_ball_number=None,
    )

    assert state.total_score is None
    assert state.next_frame_number is None
    assert state.next_ball_number is None

    dumped_json = state.model_dump_json()
    assert '"total_score":null' in dumped_json
    assert '"next_frame_number":null' in dumped_json
    assert '"next_ball_number":null' in dumped_json

    schema = GameStateResponse.model_json_schema()
    for field in ("total_score", "next_frame_number", "next_ball_number"):
        assert _is_nullable_int_schema(schema["properties"][field]), field


def test_game_state_response_nullable_fields_also_accept_a_real_int():
    # The alias must not have silently made these fields int-only or
    # accidentally optional-only; both sides of the union still work.
    resolved_frame = FrameStateResponse(
        number=1,
        rolls=[10],
        is_strike=True,
        is_spare=False,
        is_complete=True,
        score=10,
        roll_symbols=["X"],
    )
    state = GameStateResponse(
        standing_pin_ids=[],
        frames=[resolved_frame],
        total_score=10,
        is_game_complete=False,
        next_frame_number=2,
        next_ball_number=1,
    )
    assert state.total_score == 10
    assert state.next_frame_number == 2
    assert state.next_ball_number == 1
    assert '"total_score":10' in state.model_dump_json()
