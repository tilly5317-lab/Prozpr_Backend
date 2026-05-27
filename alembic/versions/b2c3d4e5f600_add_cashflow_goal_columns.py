"""Add cashflow-engine columns to goals table.

The FinancialGoal ORM model includes cashflow-engine fields (goal_type,
goal_date, goal_value_pv, etc.) that were never migrated to the DB.
This revision adds them as nullable columns so existing rows are unaffected.

Revision ID: b2c3d4e5f600
Revises: a1d3f5b7c901, d2e9f4c1a701
Create Date: 2026-05-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f600"
down_revision: Union[str, Sequence[str]] = ("a1d3f5b7c901", "d2e9f4c1a701")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if not insp.has_table("goals"):
        return

    cols = {c["name"] for c in insp.get_columns("goals")}

    # Create the cashflow goal type enum if it doesn't exist.
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE cashflow_goal_type_enum AS ENUM ("
        "'retirement','property','child_abroad_education',"
        "'child_local_education','child_marriage','custom'"
        "); EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )

    _add = []

    if "name" not in cols:
        _add.append(sa.Column("name", sa.String(100), nullable=True))
    if "goal_type" not in cols:
        _add.append(
            sa.Column(
                "goal_type",
                sa.Enum(
                    "retirement", "property", "child_abroad_education",
                    "child_local_education", "child_marriage", "custom",
                    name="cashflow_goal_type_enum", create_type=False,
                ),
                nullable=True,
            )
        )
    if "goal_date" not in cols:
        _add.append(sa.Column("goal_date", sa.Date(), nullable=True))
    if "goal_value_pv" not in cols:
        _add.append(sa.Column("goal_value_pv", sa.Numeric(18, 2), nullable=True))
    if "goal_value_fv" not in cols:
        _add.append(sa.Column("goal_value_fv", sa.Numeric(18, 2), nullable=True))
    if "target_pv" not in cols:
        _add.append(sa.Column("target_pv", sa.Numeric(18, 2), nullable=True))
    if "target_fv" not in cols:
        _add.append(sa.Column("target_fv", sa.Numeric(18, 2), nullable=True))
    if "is_downpayment_only" not in cols:
        _add.append(
            sa.Column("is_downpayment_only", sa.Boolean(), nullable=True, server_default="false")
        )
    if "upfront_amount" not in cols:
        _add.append(sa.Column("upfront_amount", sa.Numeric(18, 2), nullable=True))
    if "downpayment_pct" not in cols:
        _add.append(sa.Column("downpayment_pct", sa.Numeric(7, 6), nullable=True))
    if "inflation_annual" not in cols:
        _add.append(sa.Column("inflation_annual", sa.Numeric(7, 6), nullable=True))
    if "mortgage_tenure_years" not in cols:
        _add.append(sa.Column("mortgage_tenure_years", sa.Integer(), nullable=True))
    if "mortgage_interest_annual" not in cols:
        _add.append(sa.Column("mortgage_interest_annual", sa.Numeric(7, 6), nullable=True))
    if "date_of_birth" not in cols:
        _add.append(sa.Column("date_of_birth", sa.Date(), nullable=True))
    if "time_to_goal_months" not in cols:
        _add.append(sa.Column("time_to_goal_months", sa.Integer(), nullable=True))
    if "amount_needed" not in cols:
        _add.append(sa.Column("amount_needed", sa.Numeric(18, 2), nullable=True))
    if "goal_priority" not in cols:
        _add.append(sa.Column("goal_priority", sa.String(40), nullable=True))
    if "investment_goal" not in cols:
        _add.append(sa.Column("investment_goal", sa.String(60), nullable=True))

    for col in _add:
        op.add_column("goals", col)

    # Backfill: set `name` from `goal_name` for existing rows.
    if "name" not in cols:
        op.execute("UPDATE goals SET name = goal_name WHERE name IS NULL AND goal_name IS NOT NULL")


def downgrade() -> None:
    cols_to_drop = [
        "name", "goal_type", "goal_date", "goal_value_pv", "goal_value_fv",
        "target_pv", "target_fv", "is_downpayment_only", "upfront_amount",
        "downpayment_pct", "inflation_annual", "mortgage_tenure_years",
        "mortgage_interest_annual", "date_of_birth", "time_to_goal_months",
        "amount_needed", "goal_priority", "investment_goal",
    ]
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("goals"):
        return
    existing = {c["name"] for c in insp.get_columns("goals")}
    for col in cols_to_drop:
        if col in existing:
            op.drop_column("goals", col)
    op.execute("DROP TYPE IF EXISTS cashflow_goal_type_enum")
