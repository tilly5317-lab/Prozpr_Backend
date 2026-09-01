"""add origin to rebalancing_runs

Revision ID: 9ebf3665fe81
Revises: e4f5a6b7c8d9
Create Date: 2026-08-29 00:51:49.456480
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9ebf3665fe81'
down_revision: Union[str, None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rebalancing_runs",
        sa.Column("origin", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "ix_rebalancing_runs_origin", "rebalancing_runs", ["origin"]
    )


def downgrade() -> None:
    op.drop_index("ix_rebalancing_runs_origin", table_name="rebalancing_runs")
    op.drop_column("rebalancing_runs", "origin")
