"""Add per-goal monthly SIP and current portfolio corpus.

- ``goals.monthly_contribution`` persists the per-goal monthly SIP the user
  plans to contribute (previously frontend-only / dropped on the API).
- ``personal_finance_profiles.current_portfolio_corpus`` stores the current
  mutual-fund portfolio value (prefilled from CAMS / the portfolio page); it is
  summed with ``financial_assets`` to form the cashflow engine's starting corpus.

Both columns are nullable and additive, so this is backward-compatible.

Revision ID: b7d4e2f1a9c3
Revises: e1b2c3d4f5a6
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2f1a9c3"
down_revision: Union[str, None] = "e1b2c3d4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column("monthly_contribution", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "personal_finance_profiles",
        sa.Column("current_portfolio_corpus", sa.Numeric(18, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("personal_finance_profiles", "current_portfolio_corpus")
    op.drop_column("goals", "monthly_contribution")
