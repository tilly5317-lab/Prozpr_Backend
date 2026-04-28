"""Add payload columns to chat_ai_module_runs.

Revision ID: ee8987d840c5
Revises: e6f7a9b0c024
Create Date: 2026-04-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ee8987d840c5"
down_revision: Union[str, None] = "e6f7a9b0c024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_ai_module_runs",
        sa.Column("input_payload", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "chat_ai_module_runs",
        sa.Column("output_payload", postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "ix_chat_ai_module_runs_session_module_created",
        "chat_ai_module_runs",
        ["session_id", "module", sa.text("created_at DESC")],
        postgresql_where=sa.text("output_payload IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_ai_module_runs_session_module_created", table_name="chat_ai_module_runs")
    op.drop_column("chat_ai_module_runs", "output_payload")
    op.drop_column("chat_ai_module_runs", "input_payload")
