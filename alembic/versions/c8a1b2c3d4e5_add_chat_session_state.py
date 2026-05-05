"""Add chat_session_state table.

Revision ID: c8a1b2c3d4e5
Revises: b7e9c4f01a23
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8a1b2c3d4e5"
down_revision: Union[str, None] = "b7e9c4f01a23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_session_state",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("awaiting_save", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "last_counterfactual_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_ai_module_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.id"], ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_session_state")
