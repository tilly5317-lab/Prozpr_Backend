"""Add formatter columns to chat_ai_module_runs.

Revision ID: 5fb4cf571bdd
Revises: c5d6e7f8a900
Create Date: 2026-05-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5fb4cf571bdd"
down_revision: Union[str, None] = "c5d6e7f8a900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_ai_module_runs", sa.Column("formatter_invoked", sa.Boolean(), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("formatter_succeeded", sa.Boolean(), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("formatter_latency_ms", sa.Integer(), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("formatter_error_class", sa.String(length=128), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("action_mode", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_ai_module_runs", "action_mode")
    op.drop_column("chat_ai_module_runs", "formatter_error_class")
    op.drop_column("chat_ai_module_runs", "formatter_latency_ms")
    op.drop_column("chat_ai_module_runs", "formatter_succeeded")
    op.drop_column("chat_ai_module_runs", "formatter_invoked")
