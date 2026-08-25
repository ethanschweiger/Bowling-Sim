"""Storage-safe conversion between `GameSessionRecord` and plain,
JSON-compatible primitives — dicts, lists, `str`, `int`, `float`, nothing
else.

`GameSessionRecord` (see `app.games.service`) already holds everything
needed to rebuild a live `GameSession`, but its fields are live Python
values: a `LaneCondition` (itself nesting an `OilPatternSpec` and a
tuple-of-tuples oil grid), a `frozenset` of standing pin IDs, a `tuple` of
rolls. None of that crosses a real storage boundary (a database row, a
JSON column, a message payload) as-is. `record_to_payload`/
`record_from_payload` are that boundary: a pure, framework-independent
conversion with no FastAPI, no database driver, no file I/O anywhere in
this module. A future persistent `GameSessionRepository` is expected to
call `record_to_payload` before writing and `record_from_payload` after
reading — this module doesn't know or care what actually stores the
result.

## Where validation lives

This module only validates *shape*: is a required key present, is a
value the right primitive type, does `oil_grid` have the right number of
rows and columns. It deliberately does not re-validate *domain* rules
`Scorecard`/`Rack` already own — a payload's `rolls` can contain any
plain `int`, and its `standing_pin_ids` any plain `int`, with no 0-10 or
1-10 range check here. Round-tripping a payload through
`record_from_payload` and then `GameSession.from_record` still raises the
exact same `ScorecardError`/`RackError` an invalid live record always
did (see `GameSessionRecord`'s own module docs) — this module isn't
where that check would belong a second time, only structurally in front
of it.

The one exception is `standing_pin_ids` duplicate detection: a
duplicate-containing payload list silently collapses to a smaller
`frozenset` the moment it's built, before `Rack` (which validates
duplicates in whatever iterable *it* receives) ever sees the lost
information. That specific check has to happen here, on the raw list,
or a corrupted payload with a repeated pin ID would rehydrate silently
instead of failing loudly — see `_standing_pin_ids_from_payload`.

`GameSessionPayloadError` is this module's one exception type, raised
for anything structurally wrong with a payload: a missing key, a value
of the wrong type, a malformed `oil_grid` shape, or a duplicate standing
pin ID. `record_from_payload` raises it before returning anything —
never a partially built `GameSessionRecord`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.games.service import GameSessionRecord
from app.physics.lane import BOARD_COUNT, GRID_LENGTH_CELLS, LaneCondition, OilPatternSpec


class GameSessionPayloadError(Exception):
    """Raised by `record_from_payload` for any structurally invalid
    payload — a missing required key, a value of the wrong primitive
    type, a malformed `oil_grid` shape, or a duplicate standing pin ID.
    Never raised by `record_to_payload`, which only ever reads an
    already-valid `GameSessionRecord`."""


def _require(payload: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in payload:
        raise GameSessionPayloadError(f"{context} is missing required key {key!r}")
    return payload[key]


def _as_str(value: object, field: str) -> str:
    if type(value) is not str:
        raise GameSessionPayloadError(f"{field} must be a str, got {value!r}")
    return value


def _as_float(value: object, field: str) -> float:
    # bool is an int subclass -- explicitly excluded so a stray `true`/
    # `false` in a payload isn't silently accepted as 1.0/0.0, the same
    # "exact type, not isinstance" discipline app.physics.rack.validate_pin_ids
    # already applies to pin IDs.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameSessionPayloadError(f"{field} must be a real number, got {value!r}")
    return float(value)


def _as_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise GameSessionPayloadError(f"{field} must be a plain int, got {value!r}")
    return value


def _as_int_pair(value: object, field: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise GameSessionPayloadError(f"{field} must be a 2-element list, got {value!r}")
    return (_as_int(value[0], f"{field}[0]"), _as_int(value[1], f"{field}[1]"))


def _oil_pattern_spec_to_payload(spec: OilPatternSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "length_ft": spec.length_ft,
        "taper_ft": spec.taper_ft,
        "center_boards": list(spec.center_boards),
        "total_boards": list(spec.total_boards),
        "pattern_ratio": spec.pattern_ratio,
        "total_volume_ml": spec.total_volume_ml,
    }


def _oil_pattern_spec_from_payload(payload: object, context: str) -> OilPatternSpec:
    if not isinstance(payload, Mapping):
        raise GameSessionPayloadError(f"{context} must be an object, got {payload!r}")
    return OilPatternSpec(
        name=_as_str(_require(payload, "name", context), f"{context}.name"),
        length_ft=_as_float(_require(payload, "length_ft", context), f"{context}.length_ft"),
        taper_ft=_as_float(_require(payload, "taper_ft", context), f"{context}.taper_ft"),
        center_boards=_as_int_pair(
            _require(payload, "center_boards", context), f"{context}.center_boards"
        ),
        total_boards=_as_int_pair(
            _require(payload, "total_boards", context), f"{context}.total_boards"
        ),
        pattern_ratio=_as_float(
            _require(payload, "pattern_ratio", context), f"{context}.pattern_ratio"
        ),
        total_volume_ml=_as_float(
            _require(payload, "total_volume_ml", context), f"{context}.total_volume_ml"
        ),
    )


def _oil_grid_to_payload(oil_grid: tuple[tuple[float, ...], ...]) -> list[list[float]]:
    return [list(row) for row in oil_grid]


def _oil_grid_from_payload(payload: object, context: str) -> tuple[tuple[float, ...], ...]:
    field = f"{context}.oil_grid"
    if not isinstance(payload, list) or len(payload) != BOARD_COUNT:
        raise GameSessionPayloadError(
            f"{field} must be a list of {BOARD_COUNT} rows, got {payload!r}"
        )

    rows = []
    for board_index, row in enumerate(payload):
        if not isinstance(row, list) or len(row) != GRID_LENGTH_CELLS:
            raise GameSessionPayloadError(
                f"{field}[{board_index}] must be a list of {GRID_LENGTH_CELLS} values, got {row!r}"
            )
        rows.append(
            tuple(
                _as_float(cell, f"{field}[{board_index}][{cell_index}]")
                for cell_index, cell in enumerate(row)
            )
        )
    return tuple(rows)


def _lane_condition_to_payload(condition: LaneCondition) -> dict[str, Any]:
    return {
        "spec": _oil_pattern_spec_to_payload(condition.spec),
        "oil_grid": _oil_grid_to_payload(condition.oil_grid),
        "peak_oil_ml": condition.peak_oil_ml,
        "temperature_f": condition.temperature_f,
        "version": condition.version,
    }


def _lane_condition_from_payload(payload: object, context: str) -> LaneCondition:
    if not isinstance(payload, Mapping):
        raise GameSessionPayloadError(f"{context} must be an object, got {payload!r}")
    return LaneCondition(
        spec=_oil_pattern_spec_from_payload(
            _require(payload, "spec", context), f"{context}.spec"
        ),
        oil_grid=_oil_grid_from_payload(_require(payload, "oil_grid", context), context),
        peak_oil_ml=_as_float(_require(payload, "peak_oil_ml", context), f"{context}.peak_oil_ml"),
        temperature_f=_as_float(
            _require(payload, "temperature_f", context), f"{context}.temperature_f"
        ),
        version=_as_int(_require(payload, "version", context), f"{context}.version"),
    )


def _rolls_from_payload(payload: object) -> tuple[int, ...]:
    if not isinstance(payload, list):
        raise GameSessionPayloadError(f"rolls must be a list, got {payload!r}")
    return tuple(_as_int(pins, f"rolls[{index}]") for index, pins in enumerate(payload))


def _standing_pin_ids_from_payload(payload: object) -> frozenset[int]:
    if not isinstance(payload, list):
        raise GameSessionPayloadError(f"standing_pin_ids must be a list, got {payload!r}")

    seen: set[int] = set()
    for index, pin_id in enumerate(payload):
        validated = _as_int(pin_id, f"standing_pin_ids[{index}]")
        # See "Where validation lives" above -- this duplicate check can't
        # be deferred to Rack, which never sees a duplicate that already
        # collapsed away inside a frozenset built before it runs.
        if validated in seen:
            raise GameSessionPayloadError(f"standing_pin_ids has a duplicate entry: {validated}")
        seen.add(validated)
    return frozenset(seen)


def record_to_payload(record: GameSessionRecord) -> dict[str, Any]:
    """Converts a `GameSessionRecord` to plain, JSON-compatible
    primitives: nested dicts/lists of `str`/`int`/`float`. Always
    succeeds — a `GameSessionRecord` is already a fully valid, immutable
    value by the time one exists, so there is nothing here to reject.
    `standing_pin_ids` is sorted for a deterministic payload; the
    unordered `frozenset` it came from has no meaningful order to
    preserve.
    """
    return {
        "game_id": record.game_id,
        "initial_condition": _lane_condition_to_payload(record.initial_condition),
        "current_condition": _lane_condition_to_payload(record.current_condition),
        "rolls": list(record.rolls),
        "standing_pin_ids": sorted(record.standing_pin_ids),
    }


def record_from_payload(payload: Mapping[str, Any]) -> GameSessionRecord:
    """Rebuilds a `GameSessionRecord` from plain primitives — the inverse
    of `record_to_payload`. Raises `GameSessionPayloadError` for any
    structural problem (see this module's own docstring for exactly what
    counts as structural here versus what stays `GameSession.from_record`'s
    job) before returning anything; there is no partially built record a
    caller could observe.
    """
    if not isinstance(payload, Mapping):
        raise GameSessionPayloadError(f"payload must be an object, got {payload!r}")
    return GameSessionRecord(
        game_id=_as_str(_require(payload, "game_id", "payload"), "payload.game_id"),
        initial_condition=_lane_condition_from_payload(
            _require(payload, "initial_condition", "payload"), "payload.initial_condition"
        ),
        current_condition=_lane_condition_from_payload(
            _require(payload, "current_condition", "payload"), "payload.current_condition"
        ),
        rolls=_rolls_from_payload(_require(payload, "rolls", "payload")),
        standing_pin_ids=_standing_pin_ids_from_payload(
            _require(payload, "standing_pin_ids", "payload")
        ),
    )
