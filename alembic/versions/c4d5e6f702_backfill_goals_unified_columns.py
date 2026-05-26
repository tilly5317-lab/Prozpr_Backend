"""Backfill goals legacy and cashflow columns (nullable, non-breaking).

Keeps all columns optional. Copies values between legacy (goal_name,
present_value_amount, target_date) and cashflow (name, goal_value_pv, goal_date)
so both API and cashflow engine can read existing rows.

Revision ID: c4d5e6f702
Revises: b2c3d4e5f600
Create Date: 2026-05-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "c4d5e6f702"
down_revision: Union[str, None] = "b2c3d4e5f600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Names (both directions)
    op.execute(
        """
        UPDATE goals SET goal_name = name
        WHERE goal_name IS NULL AND name IS NOT NULL AND TRIM(name) <> ''
        """
    )
    op.execute(
        """
        UPDATE goals SET name = goal_name
        WHERE name IS NULL AND goal_name IS NOT NULL AND TRIM(goal_name) <> ''
        """
    )

    # Amounts: legacy <-> cashflow PV columns
    op.execute(
        """
        UPDATE goals SET present_value_amount = COALESCE(
            present_value_amount, goal_value_pv, target_pv, amount_needed
        )
        WHERE present_value_amount IS NULL
        """
    )
    op.execute(
        """
        UPDATE goals SET goal_value_pv = COALESCE(goal_value_pv, present_value_amount, target_pv)
        WHERE goal_value_pv IS NULL AND present_value_amount IS NOT NULL
        """
    )

    # Dates
    op.execute(
        """
        UPDATE goals SET target_date = goal_date
        WHERE target_date IS NULL AND goal_date IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE goals SET goal_date = target_date
        WHERE goal_date IS NULL AND target_date IS NOT NULL
        """
    )

    # Sensible defaults where still null (columns stay nullable for new partial rows)
    op.execute(
        """
        UPDATE goals SET inflation_rate = 6.0
        WHERE inflation_rate IS NULL
        """
    )
    op.execute(
        """
        UPDATE goals SET priority = 'HIGH'::goal_priority_enum_v2
        WHERE priority IS NULL
        """
    )
    op.execute(
        """
        UPDATE goals SET status = 'ACTIVE'::goal_status_enum_v2
        WHERE status IS NULL
        """
    )


def downgrade() -> None:
    # Data backfill is not reversed.
    pass
