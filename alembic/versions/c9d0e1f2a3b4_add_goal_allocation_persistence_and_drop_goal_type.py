"""Add lean goal-allocation persistence table and remove goals.goal_type.

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "goal_allocation_recommendations",
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
            sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_investable_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("equity_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("debt_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("others_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("equity_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("debt_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("others_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("suggested_funds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggested_funds_total_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_goal_allocation_recommendations_user_id",
        "goal_allocation_recommendations",
        ["user_id"],
    )
    op.create_index(
        "ix_goal_allocation_recommendations_portfolio_id",
        "goal_allocation_recommendations",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_goal_allocation_recommendations_chat_session_id",
        "goal_allocation_recommendations",
        ["chat_session_id"],
    )

    op.drop_column("goals", "goal_type")
    op.execute("DROP TYPE IF EXISTS goal_type_enum")


def downgrade() -> None:
    op.execute("DO $$ BEGIN CREATE TYPE goal_type_enum AS ENUM ('RETIREMENT','CHILD_EDUCATION','HOME_PURCHASE','VEHICLE','WEDDING','TRAVEL','EMERGENCY_FUND','WEALTH_CREATION','OTHER'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.add_column(
        "goals",
        sa.Column(
            "goal_type",
            postgresql.ENUM(name="goal_type_enum", create_type=False),
            nullable=True,
            server_default="OTHER",
        ),
    )
    op.execute("UPDATE goals SET goal_type = 'OTHER' WHERE goal_type IS NULL;")
    op.alter_column("goals", "goal_type", nullable=False)

    op.drop_index(
        "ix_goal_allocation_recommendations_chat_session_id",
        table_name="goal_allocation_recommendations",
    )
    op.drop_index(
        "ix_goal_allocation_recommendations_portfolio_id",
        table_name="goal_allocation_recommendations",
    )
    op.drop_index(
        "ix_goal_allocation_recommendations_user_id",
        table_name="goal_allocation_recommendations",
    )
    op.drop_table("goal_allocation_recommendations")
