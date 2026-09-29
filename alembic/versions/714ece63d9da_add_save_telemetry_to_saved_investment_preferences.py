"""applied_defaults + shortfall_reason on saved_investment_preferences (S2d Task 2).

Real save telemetry for a candidate row, captured at what-if time (when the
engine output is in hand) and read back at confirm instead of a numeric
proxy: `applied_defaults` mirrors the resolver's applied-defaults dict (same
shape the screen path already sends to capture_preference_saved);
`shortfall_reason` is the engine's own reason string. Follows the unapplied
S1 (`0ac0a4e4fc5f`) / S2 (`b7c1d2e3f4a5`) chain — this migration is ALSO
unapplied; no rows exist yet.

Revision ID: 714ece63d9da
Revises: b7c1d2e3f4a5
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "714ece63d9da"
down_revision: Union[str, None] = "b7c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_investment_preferences",
        sa.Column(
            "applied_defaults",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "saved_investment_preferences",
        sa.Column("shortfall_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("saved_investment_preferences", "shortfall_reason")
    op.drop_column("saved_investment_preferences", "applied_defaults")
