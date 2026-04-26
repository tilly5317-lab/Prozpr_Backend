"""add chart_payloads to chat_messages

Revision ID: a1b2c3d4e5f0
Revises: e6f7a9b0c024
Create Date: 2026-04-26

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f0"
down_revision = "e6f7a9b0c024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "chart_payloads",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "chart_payloads")
