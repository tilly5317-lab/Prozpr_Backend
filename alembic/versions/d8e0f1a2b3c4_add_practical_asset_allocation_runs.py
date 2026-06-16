"""Add practical_asset_allocation_runs table.

Revision ID: d8e0f1a2b3c4
Revises: c4d5e6f702
Create Date: 2026-06-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e0f1a2b3c4"
down_revision: Union[str, None] = "c4d5e6f702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "practical_asset_allocation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_source",
            sa.String(length=80),
            nullable=False,
            server_default="practical_asset_allocation",
        ),
        sa.Column("user_question", sa.Text(), nullable=True),
        # client summary
        sa.Column("client_age", sa.Integer(), nullable=False),
        sa.Column("client_occupation", sa.String(length=80), nullable=True),
        sa.Column("client_effective_risk_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("total_corpus", sa.Numeric(18, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "all_amounts_in_multiples_of_100",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        # recommended asset-class roll-up
        sa.Column(
            "equity_total", sa.Numeric(18, 2), nullable=False, server_default="0"
        ),
        sa.Column("debt_total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column(
            "others_total", sa.Numeric(18, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "equity_total_pct", sa.Numeric(7, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "debt_total_pct", sa.Numeric(7, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "others_total_pct", sa.Numeric(7, 2), nullable=False, server_default="0"
        ),
        # practical-only corpus breakdown
        sa.Column("mf_corpus", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column(
            "non_mf_equity_input", sa.Numeric(18, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "non_mf_equity_actual",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "excess_direct_stocks",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("elss_corpus", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column(
            "rebalancing_corpus", sa.Numeric(18, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "max_non_mf_equity_pct_computed",
            sa.Numeric(6, 4),
            nullable=False,
            server_default="0",
        ),
        # payloads
        sa.Column(
            "input_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_practical_asset_allocation_runs_user_id",
        "practical_asset_allocation_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_practical_asset_allocation_runs_chat_session_id",
        "practical_asset_allocation_runs",
        ["chat_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_practical_asset_allocation_runs_chat_session_id",
        table_name="practical_asset_allocation_runs",
    )
    op.drop_index(
        "ix_practical_asset_allocation_runs_user_id",
        table_name="practical_asset_allocation_runs",
    )
    op.drop_table("practical_asset_allocation_runs")
