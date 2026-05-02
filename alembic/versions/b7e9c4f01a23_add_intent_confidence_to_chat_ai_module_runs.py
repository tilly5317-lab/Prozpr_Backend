"""Add intent_confidence to chat_ai_module_runs.

Revision ID: b7e9c4f01a23
Revises: 5fb4cf571bdd
Create Date: 2026-05-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e9c4f01a23"
down_revision: Union[str, None] = "5fb4cf571bdd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_ai_module_runs",
        sa.Column("intent_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_ai_module_runs", "intent_confidence")
