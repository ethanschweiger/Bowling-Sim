"""Row-value and SQL-statement helpers for the `game_sessions` table —
the boundary between a `GameSessionRecord` and what a future SQLAlchemy
repository would actually send to (or read back from) PostgreSQL.

Deliberately engine-free: nothing in this module creates an `Engine`, a
`Session`, or a connection, and no function here can open one. Every
function is a pure conversion or a statement-builder — a `Select`/
`Insert` object is data describing a query, not a query that has run.
Reading a `game_sessions` row and turning it into a live `GameSession`,
or writing one back, is a future runtime repository's job, entirely
outside this module — the same boundary `app.games.record_payload`
already draws around turning a `GameSessionRecord` into JSON-compatible
primitives, one layer further out.

## Layering

This module doesn't invent a second serialized shape for a game's
state. `record_to_row_values`/`record_from_row` sit on top of the
existing `app.games.record_payload.record_to_payload`/
`record_from_payload` pair: a row's `payload` column is exactly what
`record_to_payload` already produces, and `record_from_row` hands
whatever it finds there straight to `record_from_payload` rather than
re-implementing its structural validation. What this module adds on top
is specifically about the *row*, not the payload's own shape: a
`payload_version` column (versioning the row format itself, so a future
schema change can tell an old row from a new one before trying to
decode it) and consistency between the row's own `game_id` primary key
and the `game_id` embedded inside its `payload`.

`PAYLOAD_VERSION` is the one row format this module knows how to write
and read today. `record_from_row` rejects any other stored
`payload_version` outright — not because a different version is
necessarily invalid, but because nothing here has ever been taught how
to interpret one; growing a second version's read path is a future
change, not something to guess at now.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.schema import game_sessions
from app.games.record_payload import record_from_payload, record_to_payload
from app.games.service import GameSessionRecord

# The only row format this module writes or reads. A future format change
# (a new payload_version) is a new, additional code path -- see this
# module's own docstring -- not a bump to this constant that silently
# changes what already-stored rows mean.
PAYLOAD_VERSION = 1


class GameSessionRowError(Exception):
    """Raised by `record_from_row` for a stored row this module can't
    turn into a `GameSessionRecord`: an unsupported `payload_version`, a
    missing or non-mapping `payload`, or a `payload` whose own `game_id`
    disagrees with the row's primary key. Never raised by
    `record_to_row_values`, which only ever reads an already-valid
    `GameSessionRecord` (the same guarantee `record_to_payload` already
    makes -- see that function's own docstring).
    """


def record_to_row_values(record: GameSessionRecord) -> dict[str, Any]:
    """The column values one `game_sessions` row needs to store
    `record`: `game_id` (the row's own primary key), `payload` (via
    `record_to_payload`), and `payload_version` (always the current
    `PAYLOAD_VERSION` -- this module only ever writes the format it
    itself knows how to read back). Deliberately omits `created_at`/
    `updated_at`; both are left to the table's own database-side
    defaults (see `app.db.schema`), not something application code
    stamps a value into.
    """
    return {
        "game_id": record.game_id,
        "payload": record_to_payload(record),
        "payload_version": PAYLOAD_VERSION,
    }


def record_from_row(row: Mapping[str, Any]) -> GameSessionRecord:
    """Rebuilds a `GameSessionRecord` from a loaded `game_sessions` row
    (any `str`-keyed mapping with `game_id`, `payload`, and
    `payload_version` -- a SQLAlchemy `RowMapping` satisfies this without
    conversion). The inverse of `record_to_row_values`, layered on top of
    `record_from_payload` rather than duplicating its validation.

    Raises `GameSessionRowError` for anything specific to the *row*:
    - `payload_version` missing or not equal to `PAYLOAD_VERSION`.
    - `payload` missing or not a mapping.
    - the payload's own `game_id` disagreeing with the row's `game_id`.

    Raises `GameSessionPayloadError` (from `record_from_payload`) for
    anything wrong with the payload's own structure -- a missing key, a
    malformed `oil_grid`, and so on; this function doesn't re-check any
    of that itself. Raises neither, and returns a valid
    `GameSessionRecord`, only once every check has passed.
    """
    if "payload_version" not in row:
        raise GameSessionRowError("row is missing required key 'payload_version'")
    payload_version = row["payload_version"]
    if payload_version != PAYLOAD_VERSION:
        raise GameSessionRowError(
            f"unsupported payload_version {payload_version!r} (this code only reads "
            f"{PAYLOAD_VERSION!r})"
        )

    if "payload" not in row:
        raise GameSessionRowError("row is missing required key 'payload'")
    payload = row["payload"]
    if not isinstance(payload, Mapping):
        raise GameSessionRowError(f"row's payload must be a mapping, got {payload!r}")

    if "game_id" not in row:
        raise GameSessionRowError("row is missing required key 'game_id'")
    row_game_id = row["game_id"]
    payload_game_id = payload.get("game_id")
    if row_game_id != payload_game_id:
        raise GameSessionRowError(
            f"row game_id {row_game_id!r} does not match payload game_id {payload_game_id!r}"
        )

    return record_from_payload(payload)


def select_game_session_stmt(game_id: str) -> sa.Select[Any]:
    """A `SELECT` for the one `game_sessions` row stored under `game_id`
    -- a statement to compile or execute, not a result. Zero or one row;
    `game_id` is the table's primary key."""
    return sa.select(game_sessions).where(game_sessions.c.game_id == game_id)


def upsert_game_session_stmt(record: GameSessionRecord) -> PgInsert:
    """A PostgreSQL `INSERT ... ON CONFLICT (game_id) DO UPDATE` for
    `record` -- a statement to compile or execute, not a completed
    write. On a fresh `game_id` this inserts a new row (`created_at`
    taking the table's own database-side default). On an existing
    `game_id` this updates `payload`, `payload_version`, and
    `updated_at` (stamped to the moment of the write) in place --
    `created_at` is deliberately absent from the update clause, so an
    existing row's original `created_at` is never touched by a later
    upsert.
    """
    stmt = pg_insert(game_sessions).values(**record_to_row_values(record))
    return stmt.on_conflict_do_update(
        index_elements=[game_sessions.c.game_id],
        set_={
            "payload": stmt.excluded.payload,
            "payload_version": stmt.excluded.payload_version,
            "updated_at": sa.func.now(),
        },
    )
