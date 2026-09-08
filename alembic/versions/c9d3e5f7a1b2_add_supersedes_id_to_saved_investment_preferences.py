"""supersedes_id on saved_investment_preferences (lineage).

The row this one REPLACED when it became active — a nullable self-FK,
written once at activation (screen save or chat "yes, save it"). Backward
pointer, same convention as `asset_allocation_runs.supersedes_id` /
`rebalancing_runs.supersedes_id`, so the replaced row is never touched
and the immutability contract holds. NULL = first save, save-after-clear,
or a candidate not yet activated. No index: lineage is read one hop at a
time by PK. Follows the unapplied S1/S2/S2d chain; on the dev RDS this
lands via targeted DDL alongside them.

Revision ID: c9d3e5f7a1b2
Revises: 714ece63d9da
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d3e5f7a1b2"
down_revision: Union[str, None] = "714ece63d9da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_investment_preferences",
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("saved_investment_preferences.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("saved_investment_preferences", "supersedes_id")
