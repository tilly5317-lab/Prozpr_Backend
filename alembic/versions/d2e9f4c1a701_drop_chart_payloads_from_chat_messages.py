"""Drop chart_payloads from chat_messages.

Revision ID: d2e9f4c1a701
Revises: c8a1b2c3d4e5
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2e9f4c1a701"
down_revision: Union[str, None] = "c8a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("chat_messages", "chart_payloads")


def downgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("chart_payloads", postgresql.JSONB, nullable=True),
    )
