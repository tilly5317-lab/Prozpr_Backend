"""Add additional_investment_runs and its two child tables (BUY-only deploy engine).

Mirrors the rebalancing run/child family (revision d6e7f8a90b12) for the
additional-investment app domain: a write-once run header plus two children -
the per-subgroup deploy targets and the per-fund BUY list. The engine is
BUY-only and write-once, so there is no status-lifecycle enum.

``source_allocation_run_id`` FKs the live ``practical_asset_allocation_runs``
table with ondelete RESTRICT, NOT NULL (Finding 1, Option B): the deploy split is
derived from the practical allocation, which the orchestrator persists inline so
the id is always present. Money is float into Numeric(18, 2) - the allocation
family, not Rebalancing's Decimal - because there is no tax-lot math here.

Revision ID: c1d2e3f4a5b6
Revises: f5c2a1b3d8e7
Create Date: 2026-06-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "f5c2a1b3d8e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Enum definitions (created once; reused on the run table) --

ADDITIONAL_INVESTMENT_TARGET_BUCKET = postgresql.ENUM(
    "short_term",
    "medium_term",
    "long_term",
    name="additional_investment_target_bucket_enum",
    create_type=False,
)
ADDITIONAL_INVESTMENT_CADENCE = postgresql.ENUM(
    "lumpsum",
    "sip_monthly",
    name="additional_investment_cadence_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ADDITIONAL_INVESTMENT_TARGET_BUCKET.create(bind, checkfirst=True)
    ADDITIONAL_INVESTMENT_CADENCE.create(bind, checkfirst=True)

    # 1. additional_investment_runs (write-once header; BUY-only, no status enum).
    op.create_table(
        "additional_investment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_allocation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("practical_asset_allocation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column("target_bucket", ADDITIONAL_INVESTMENT_TARGET_BUCKET, nullable=False),
        sa.Column("cadence", ADDITIONAL_INVESTMENT_CADENCE, nullable=False),
        sa.Column("deploy_amount_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("deployed_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("undeployed_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("request_input", postgresql.JSONB, nullable=True),
        sa.Column("used_cached_allocation", sa.Boolean(), nullable=True),
        sa.Column("user_question", sa.String(2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_additional_investment_runs_user_id",
        "additional_investment_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_additional_investment_runs_portfolio_id",
        "additional_investment_runs",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_additional_investment_runs_chat_session_id",
        "additional_investment_runs",
        ["chat_session_id"],
    )
    op.create_index(
        "ix_additional_investment_runs_source_allocation_run_id",
        "additional_investment_runs",
        ["source_allocation_run_id"],
    )

    # 2. additional_investment_targets (one row per SubgroupTarget).
    op.create_table(
        "additional_investment_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subgroup", sa.String(80), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("target_inr", sa.Numeric(18, 2), nullable=False),
    )
    op.create_index(
        "ix_additional_investment_targets_run_id",
        "additional_investment_targets",
        ["run_id"],
    )

    # 3. additional_investment_buys (one row per FundBuy).
    op.create_table(
        "additional_investment_buys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recommended_fund", sa.String(255), nullable=False),
        sa.Column("isin", sa.String(20), nullable=False),
        sa.Column("sub_category", sa.String(80), nullable=False),
        sa.Column("asset_subgroup", sa.String(80), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("scheme_code", sa.String(40), nullable=False),
        sa.Column("amount_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("monthly_amount_inr", sa.Numeric(18, 2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_additional_investment_buys_run_id",
        "additional_investment_buys",
        ["run_id"],
    )


def downgrade() -> None:
    # Drop children before the parent, then the enums.
    op.drop_index(
        "ix_additional_investment_buys_run_id",
        table_name="additional_investment_buys",
    )
    op.drop_table("additional_investment_buys")

    op.drop_index(
        "ix_additional_investment_targets_run_id",
        table_name="additional_investment_targets",
    )
    op.drop_table("additional_investment_targets")

    op.drop_index(
        "ix_additional_investment_runs_source_allocation_run_id",
        table_name="additional_investment_runs",
    )
    op.drop_index(
        "ix_additional_investment_runs_chat_session_id",
        table_name="additional_investment_runs",
    )
    op.drop_index(
        "ix_additional_investment_runs_portfolio_id",
        table_name="additional_investment_runs",
    )
    op.drop_index(
        "ix_additional_investment_runs_user_id",
        table_name="additional_investment_runs",
    )
    op.drop_table("additional_investment_runs")

    bind = op.get_bind()
    ADDITIONAL_INVESTMENT_CADENCE.drop(bind, checkfirst=True)
    ADDITIONAL_INVESTMENT_TARGET_BUCKET.drop(bind, checkfirst=True)
