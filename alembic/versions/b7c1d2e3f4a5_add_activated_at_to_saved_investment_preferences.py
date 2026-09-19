"""activated_at on saved_investment_preferences (S2).

A chat what-if is a preference row from the moment it is computed — inactive
and never activated — so the candidate run it shaped can FK it. NULL =
never saved. No backfill: the S1 migration this follows is unapplied, so no
rows exist yet.

Revision ID: b7c1d2e3f4a5
Revises: 0ac0a4e4fc5f
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, None] = "0ac0a4e4fc5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_investment_preferences",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("saved_investment_preferences", "activated_at")
