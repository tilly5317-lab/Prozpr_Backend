"""Add index_tri_history table.

Revision ID: e1a2b3c4d5e6
Revises: d8e0f1a2b3c4
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, None] = "d8e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "index_tri_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("index_name", sa.String(length=50), nullable=False),
        sa.Column("tri_date", sa.Date(), nullable=False),
        sa.Column("tri_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("ntr_value", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("index_name", "tri_date", name="uq_index_tri_name_date"),
    )
    op.create_index(
        "ix_index_tri_history_index_name", "index_tri_history", ["index_name"]
    )
    op.create_index("ix_index_tri_history_tri_date", "index_tri_history", ["tri_date"])


def downgrade() -> None:
    op.drop_index("ix_index_tri_history_tri_date", table_name="index_tri_history")
    op.drop_index("ix_index_tri_history_index_name", table_name="index_tri_history")
    op.drop_table("index_tri_history")
