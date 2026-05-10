"""Split mf_fund_metadata into source-only metadata + curated mf_fund_ratings.

Revision ID: a2c4e6f80123
Revises: f1a2b3c4d5e6
Create Date: 2026-05-06

Splits ``mf_fund_metadata`` so the source-fed identifying fields stay on
``mf_fund_metadata`` and our curated rating / dynamic data moves to a new
``mf_fund_ratings`` table linked by ``scheme_code`` (and mirroring ``isin``).
Drops the persisted period-return columns from both layers — returns are now
computed dynamically from ``mf_nav_history``.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2c4e6f80123"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RATING_COLUMNS = [
    "risk_rating_sebi",
    "asset_class_sebi",
    "asset_class",
    "asset_subgroup",
    "portfolio_managers_current",
    "portfolio_managers_history",
    "portfolio_manager_change_date",
    "rating_external_agency_1",
    "rating_external_agency_2",
    "our_rating_parameter_1",
    "our_rating_parameter_2",
    "our_rating_parameter_3",
    "our_rating_history_parameter_1",
    "our_rating_history_parameter_2",
    "our_rating_history_parameter_3",
    "direct_plan_fees",
    "regular_plan_fees",
    "entry_load_percent",
    "exit_load_percent",
    "exit_load_months",
    "large_cap_equity_pct",
    "mid_cap_equity_pct",
    "small_cap_equity_pct",
    "debt_pct",
    "others_pct",
]

_RETURNS_COLUMNS = [
    "returns_1y_pct",
    "returns_3y_pct",
    "returns_5y_pct",
    "returns_10y_pct",
]


def upgrade() -> None:
    op.create_table(
        "mf_fund_ratings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scheme_code", sa.String(length=20), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("risk_rating_sebi", sa.String(length=50), nullable=True),
        sa.Column("asset_class_sebi", sa.String(length=100), nullable=True),
        sa.Column("asset_class", sa.String(length=100), nullable=True),
        sa.Column("asset_subgroup", sa.String(length=100), nullable=True),
        sa.Column("portfolio_managers_current", sa.Text(), nullable=True),
        sa.Column("portfolio_managers_history", sa.Text(), nullable=True),
        sa.Column("portfolio_manager_change_date", sa.Date(), nullable=True),
        sa.Column("rating_external_agency_1", sa.String(length=50), nullable=True),
        sa.Column("rating_external_agency_2", sa.String(length=50), nullable=True),
        sa.Column("our_rating_parameter_1", sa.String(length=100), nullable=True),
        sa.Column("our_rating_parameter_2", sa.String(length=100), nullable=True),
        sa.Column("our_rating_parameter_3", sa.String(length=100), nullable=True),
        sa.Column("our_rating_history_parameter_1", sa.Text(), nullable=True),
        sa.Column("our_rating_history_parameter_2", sa.Text(), nullable=True),
        sa.Column("our_rating_history_parameter_3", sa.Text(), nullable=True),
        sa.Column("direct_plan_fees", sa.Numeric(6, 4), nullable=True),
        sa.Column("regular_plan_fees", sa.Numeric(6, 4), nullable=True),
        sa.Column("entry_load_percent", sa.Numeric(6, 4), nullable=True),
        sa.Column("exit_load_percent", sa.Numeric(6, 4), nullable=True),
        sa.Column("exit_load_months", sa.Integer(), nullable=True),
        sa.Column("large_cap_equity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("mid_cap_equity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("small_cap_equity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("debt_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("others_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scheme_code"], ["mf_fund_metadata.scheme_code"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("scheme_code", name="uq_mf_fund_ratings_scheme_code"),
    )
    op.create_index("ix_mf_fund_ratings_scheme_code", "mf_fund_ratings", ["scheme_code"])
    op.create_index("ix_mf_fund_ratings_isin", "mf_fund_ratings", ["isin"])
    op.create_index("ix_mf_fund_ratings_asset_class", "mf_fund_ratings", ["asset_class"])

    # Backfill curated rows from any pre-split data — only when at least one
    # rating field is non-null on the legacy mf_fund_metadata row.
    rating_cols = ", ".join(_RATING_COLUMNS)
    nonnull_clause = " OR ".join(f"{c} IS NOT NULL" for c in _RATING_COLUMNS)
    op.execute(
        f"""
        INSERT INTO mf_fund_ratings (id, scheme_code, isin, {rating_cols}, created_at, updated_at)
        SELECT gen_random_uuid(), scheme_code, isin, {rating_cols}, COALESCE(created_at, now()), now()
        FROM mf_fund_metadata
        WHERE {nonnull_clause}
        """
    )

    # Drop the legacy index if present (some environments never created it).
    op.execute("DROP INDEX IF EXISTS ix_mf_fund_metadata_amc_category")

    for col in _RATING_COLUMNS + _RETURNS_COLUMNS:
        op.drop_column("mf_fund_metadata", col)

    # Recreate the AMC/category index (category is still on metadata).
    op.create_index(
        "ix_mf_fund_metadata_amc_category",
        "mf_fund_metadata",
        ["amc_name", "category"],
    )


def downgrade() -> None:
    # Add the moved columns back to mf_fund_metadata.
    op.add_column("mf_fund_metadata", sa.Column("risk_rating_sebi", sa.String(length=50), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("asset_class_sebi", sa.String(length=100), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("asset_class", sa.String(length=100), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("asset_subgroup", sa.String(length=100), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("portfolio_managers_current", sa.Text(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("portfolio_managers_history", sa.Text(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("portfolio_manager_change_date", sa.Date(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("rating_external_agency_1", sa.String(length=50), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("rating_external_agency_2", sa.String(length=50), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("our_rating_parameter_1", sa.String(length=100), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("our_rating_parameter_2", sa.String(length=100), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("our_rating_parameter_3", sa.String(length=100), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("our_rating_history_parameter_1", sa.Text(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("our_rating_history_parameter_2", sa.Text(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("our_rating_history_parameter_3", sa.Text(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("direct_plan_fees", sa.Numeric(6, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("regular_plan_fees", sa.Numeric(6, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("entry_load_percent", sa.Numeric(6, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("exit_load_percent", sa.Numeric(6, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("exit_load_months", sa.Integer(), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("large_cap_equity_pct", sa.Numeric(6, 2), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("mid_cap_equity_pct", sa.Numeric(6, 2), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("small_cap_equity_pct", sa.Numeric(6, 2), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("debt_pct", sa.Numeric(6, 2), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("others_pct", sa.Numeric(6, 2), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("returns_1y_pct", sa.Numeric(8, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("returns_3y_pct", sa.Numeric(8, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("returns_5y_pct", sa.Numeric(8, 4), nullable=True))
    op.add_column("mf_fund_metadata", sa.Column("returns_10y_pct", sa.Numeric(8, 4), nullable=True))

    # Copy curated values back from mf_fund_ratings.
    set_clauses = ", ".join(f"{c} = r.{c}" for c in _RATING_COLUMNS)
    op.execute(
        f"""
        UPDATE mf_fund_metadata m
        SET {set_clauses}
        FROM mf_fund_ratings r
        WHERE m.scheme_code = r.scheme_code
        """
    )

    op.drop_index("ix_mf_fund_ratings_asset_class", table_name="mf_fund_ratings")
    op.drop_index("ix_mf_fund_ratings_isin", table_name="mf_fund_ratings")
    op.drop_index("ix_mf_fund_ratings_scheme_code", table_name="mf_fund_ratings")
    op.drop_table("mf_fund_ratings")
