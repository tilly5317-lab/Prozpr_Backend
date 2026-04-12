"""Effective risk assessments table + inputs on risk/investment profiles."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1c2d3e4f501"
down_revision: Union[str, None] = "a9f1c2d8e400"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "effective_risk_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_name", sa.String(64), nullable=False, server_default="risk_profile"),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("calculations", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("effective_risk_score", sa.Numeric(7, 4), nullable=True),
        sa.Column("risk_capacity_score", sa.Numeric(7, 4), nullable=True),
        sa.Column("risk_willingness", sa.Numeric(7, 4), nullable=True),
        sa.Column("trigger_reason", sa.String(64), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_effective_risk_assessments_user_id",
        "effective_risk_assessments",
        ["user_id"],
        unique=True,
    )

    op.add_column(
        "risk_profiles",
        sa.Column("risk_willingness", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "risk_profiles",
        sa.Column("occupation_type", sa.String(50), nullable=True),
    )

    op.add_column(
        "investment_profiles",
        sa.Column("annual_mortgage_payment", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "investment_profiles",
        sa.Column("properties_owned", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("investment_profiles", "properties_owned")
    op.drop_column("investment_profiles", "annual_mortgage_payment")
    op.drop_column("risk_profiles", "occupation_type")
    op.drop_column("risk_profiles", "risk_willingness")
    op.drop_index("ix_effective_risk_assessments_user_id", table_name="effective_risk_assessments")
    op.drop_table("effective_risk_assessments")
