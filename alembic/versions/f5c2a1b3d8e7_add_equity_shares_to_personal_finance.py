"""Add equity_shares to personal_finance_profiles.

Splits the user's liquid wealth into two separately-editable inputs:
``financial_assets`` now represents cash & debt instruments only, and the new
``equity_shares`` column holds listed equities / direct shares. Both are summed
into the cashflow engine's starting corpus, so the projection is unchanged for
existing users (the new column is nullable / treated as ₹0 when unset).

The column is nullable and additive, so this is backward-compatible.

Revision ID: f5c2a1b3d8e7
Revises: f4b9a1c2d3e6
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5c2a1b3d8e7"
down_revision: Union[str, None] = "f4b9a1c2d3e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "personal_finance_profiles",
        sa.Column("equity_shares", sa.Numeric(18, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("personal_finance_profiles", "equity_shares")
