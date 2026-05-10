"""Create goal_contributions and goal_holdings tables.

The ``GoalContribution`` and ``GoalHolding`` ORM models have always existed in
``app/models/goals/`` and are queried by ``app/routers/goals.py`` (see
``_goal_totals_map``), but no prior Alembic revision actually creates these
tables. On environments that rely on ``alembic upgrade head`` (i.e. production)
this manifests as a 500 on ``GET /api/v1/goals/`` whenever the user has any
goals, because the totals query joins ``goal_contributions``.

This revision is idempotent: it skips creation when the tables already exist
(e.g. local dev where ``Base.metadata.create_all`` ran first).

Revision ID: f9a0b1c2d3e4
Revises: d6e7f8a90b12
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "d6e7f8a90b12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if not insp.has_table("goal_contributions"):
        op.create_table(
            "goal_contributions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "goal_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("goals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("amount", sa.Numeric(15, 2), nullable=False),
            sa.Column(
                "contributed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_goal_contributions_goal_id", "goal_contributions", ["goal_id"]
        )
        op.execute(
            "ALTER TABLE goal_contributions ALTER COLUMN id SET DEFAULT gen_random_uuid();"
        )

    if not insp.has_table("goal_holdings"):
        op.create_table(
            "goal_holdings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "goal_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("goals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("fund_name", sa.String(255), nullable=False),
            sa.Column("category", sa.String(100), nullable=True),
            sa.Column("invested_amount", sa.Numeric(15, 2), nullable=False),
            sa.Column("current_value", sa.Numeric(15, 2), nullable=True),
            sa.Column("gain_percentage", sa.Numeric(7, 2), nullable=True),
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
        op.create_index("ix_goal_holdings_goal_id", "goal_holdings", ["goal_id"])
        op.execute(
            "ALTER TABLE goal_holdings ALTER COLUMN id SET DEFAULT gen_random_uuid();"
        )

        # Reuse the shared updated_at trigger function created in
        # e4f8a2b1c901_fintech_goals_mf_networth_schema.py.
        op.execute("DROP TRIGGER IF EXISTS trg_goal_holdings_updated_at ON goal_holdings;")
        op.execute(
            """
            CREATE TRIGGER trg_goal_holdings_updated_at
            BEFORE UPDATE ON goal_holdings
            FOR EACH ROW EXECUTE PROCEDURE set_updated_at_timestamp();
            """
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_goal_holdings_updated_at ON goal_holdings;")
    op.drop_index("ix_goal_holdings_goal_id", table_name="goal_holdings")
    op.drop_table("goal_holdings")
    op.drop_index("ix_goal_contributions_goal_id", table_name="goal_contributions")
    op.drop_table("goal_contributions")
