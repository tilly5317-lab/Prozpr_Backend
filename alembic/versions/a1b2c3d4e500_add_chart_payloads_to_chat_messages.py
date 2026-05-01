"""Add chart_payloads to chat_messages.

Revision ID: a1b2c3d4e500
Revises: 9111caff5585
Create Date: 2026-04-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e500"
down_revision: Union[str, None] = "9111caff5585"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("chart_payloads", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "chart_payloads")
