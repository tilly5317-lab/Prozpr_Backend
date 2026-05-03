"""Add isin and isin_div_reinvest columns to mf_fund_metadata.

Revision ID: f1a2b3c4d5e6
Revises: ee8987d840c5
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "ee8987d840c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mf_fund_metadata",
        sa.Column("isin", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "mf_fund_metadata",
        sa.Column("isin_div_reinvest", sa.String(length=12), nullable=True),
    )
    op.create_index("ix_mf_fund_metadata_isin", "mf_fund_metadata", ["isin"])
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mf_fund_metadata_isin_notnull "
        "ON mf_fund_metadata (isin) WHERE isin IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_mf_fund_metadata_isin_notnull")
    op.drop_index("ix_mf_fund_metadata_isin", table_name="mf_fund_metadata")
    op.drop_column("mf_fund_metadata", "isin_div_reinvest")
    op.drop_column("mf_fund_metadata", "isin")
