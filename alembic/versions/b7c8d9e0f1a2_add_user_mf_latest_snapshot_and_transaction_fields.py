"""Add transaction display fields and user_mf_latest_snapshot table.

Revision ID: b7c8d9e0f1a2
Revises: a2c4e6f80123
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a2c4e6f80123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mf_transactions", sa.Column("isin", sa.String(length=12), nullable=True))
    op.add_column("mf_transactions", sa.Column("fund_name", sa.String(length=200), nullable=True))
    op.add_column("mf_transactions", sa.Column("category", sa.String(length=50), nullable=True))
    op.add_column("mf_transactions", sa.Column("sub_category", sa.String(length=100), nullable=True))
    op.add_column("mf_transactions", sa.Column("sub_group", sa.String(length=100), nullable=True))
    op.create_index("ix_mf_transactions_isin", "mf_transactions", ["isin"])
    op.create_index("ix_mf_transactions_category", "mf_transactions", ["category"])

    op.execute(
        """
        UPDATE mf_transactions t
        SET
            isin = m.isin,
            fund_name = m.scheme_name,
            category = m.category,
            sub_category = m.sub_category,
            sub_group = r.asset_subgroup
        FROM mf_fund_metadata m
        LEFT JOIN mf_fund_ratings r ON r.scheme_code = m.scheme_code
        WHERE t.scheme_code = m.scheme_code
        """
    )

    op.create_table(
        "user_mf_latest_snapshot",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_code", sa.String(length=20), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("fund_name", sa.String(length=200), nullable=True),
        sa.Column("amc_name", sa.String(length=100), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("sub_category", sa.String(length=100), nullable=True),
        sa.Column("sub_group", sa.String(length=100), nullable=True),
        sa.Column("invested_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("current_units", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("avg_nav", sa.Numeric(12, 4), nullable=True),
        sa.Column("current_nav", sa.Numeric(12, 4), nullable=True),
        sa.Column("current_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("absolute_return_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("xirr_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("portfolio_weight_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_1y_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_3y_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_5y_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("first_investment_date", sa.Date(), nullable=True),
        sa.Column("last_transaction_date", sa.Date(), nullable=True),
        sa.Column("nav_date", sa.Date(), nullable=True),
        sa.Column("transactions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("folio_number", sa.String(length=30), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "scheme_code", name="uq_user_mf_latest_snapshot_user_scheme"),
    )
    op.create_index("ix_user_mf_latest_snapshot_user_id", "user_mf_latest_snapshot", ["user_id"])
    op.create_index("ix_user_mf_latest_snapshot_scheme_code", "user_mf_latest_snapshot", ["scheme_code"])
    op.create_index("ix_user_mf_latest_snapshot_isin", "user_mf_latest_snapshot", ["isin"])
    op.create_index("ix_user_mf_latest_snapshot_category", "user_mf_latest_snapshot", ["category"])


def downgrade() -> None:
    op.drop_index("ix_user_mf_latest_snapshot_category", table_name="user_mf_latest_snapshot")
    op.drop_index("ix_user_mf_latest_snapshot_isin", table_name="user_mf_latest_snapshot")
    op.drop_index("ix_user_mf_latest_snapshot_scheme_code", table_name="user_mf_latest_snapshot")
    op.drop_index("ix_user_mf_latest_snapshot_user_id", table_name="user_mf_latest_snapshot")
    op.drop_table("user_mf_latest_snapshot")

    op.drop_index("ix_mf_transactions_category", table_name="mf_transactions")
    op.drop_index("ix_mf_transactions_isin", table_name="mf_transactions")
    op.drop_column("mf_transactions", "sub_group")
    op.drop_column("mf_transactions", "sub_category")
    op.drop_column("mf_transactions", "category")
    op.drop_column("mf_transactions", "fund_name")
    op.drop_column("mf_transactions", "isin")
