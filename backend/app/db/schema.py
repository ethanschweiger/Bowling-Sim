"""SQLAlchemy table metadata for the `game_sessions` persistence schema —
the migration foundation this milestone adds, not a repository.

Nothing in `app.games` (or anywhere else in the running app) imports this
module, opens a database connection through it, or reads/writes a row
through it yet. `default_game_service` (`app.games.service`) still holds
its games in `InMemoryGameSessionRepository`, exactly as before — see
that module's own "The repository boundary" docs. This module exists so
Alembic has one importable `target_metadata`, and so the `game_sessions`
table's shape is described in exactly one place, ready for a future
`GameSessionRepository` implementation to actually read and write
through it.

Deliberately plain SQLAlchemy Core (`MetaData`/`Table`), not the
declarative ORM: nothing here needs a `Base` class, a session, or model
instances with attribute access — a `Table` alone is enough to describe
a schema and drive Alembic's autogenerate/offline SQL, without adding a
session-management layer this milestone explicitly doesn't want yet.

## Columns

- `game_id` — the same opaque id `GameSession.game_id` already uses,
  primary key.
- `payload` — exactly `app.games.record_payload.record_to_payload()`'s
  own JSON-compatible dict, stored as PostgreSQL `JSONB`. This table
  doesn't invent a second shape for the same data or split it across
  columns; the payload boundary already did the work of making it one
  storable value.
- `payload_version` — versions the *payload's own shape* (its top-level
  keys, e.g. if `record_to_payload` ever adds/renames a field), not
  anything inside the payload itself — `LaneCondition.version` and
  `GameSessionRecord`'s own fields are unrelated to this column and stay
  wherever `payload` already puts them.
- `created_at`/`updated_at` — ordinary row bookkeeping, `TIMESTAMPTZ`
  with a database-side `now()` default so a row's timestamps don't
  depend on the writer's own clock.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

game_sessions = sa.Table(
    "game_sessions",
    metadata,
    sa.Column("game_id", sa.String, primary_key=True),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("payload_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    ),
)
