"""Add chat_ai_module_runs for Prozpr module audit trail.

Revision ID: f7c91d2e4a00
Revises: e4f8a2b1c901
Create Date: 2026-04-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7c91d2e4a00"
down_revision: Union[str, None] = "e4f8a2b1c901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_ai_module_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("intent_detected", sa.String(length=64), nullable=True),
        sa.Column("spine_mode", sa.String(length=32), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_ai_module_runs_user_id", "chat_ai_module_runs", ["user_id"], unique=False)
    op.create_index("ix_chat_ai_module_runs_session_id", "chat_ai_module_runs", ["session_id"], unique=False)
    op.create_index("ix_chat_ai_module_runs_module", "chat_ai_module_runs", ["module"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_ai_module_runs_module", table_name="chat_ai_module_runs")
    op.drop_index("ix_chat_ai_module_runs_session_id", table_name="chat_ai_module_runs")
    op.drop_index("ix_chat_ai_module_runs_user_id", table_name="chat_ai_module_runs")
    op.drop_table("chat_ai_module_runs")
