"""create game_sessions table

Revision ID: a42df50c3040
Revises:
Create Date: 2026-08-25 14:32:35.812832

Creates the one table this milestone's schema scaffold defines -- see
`app.db.schema` for the column-by-column rationale this migration mirrors.
Spelled out explicitly with `op.create_table`/`op.drop_table` rather than
importing `app.db.schema.game_sessions` directly: a migration is a
historical record of one specific schema change, and should stay valid
even after a later commit changes what `app.db.schema` itself describes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a42df50c3040"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "game_sessions",
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("game_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("game_sessions")
