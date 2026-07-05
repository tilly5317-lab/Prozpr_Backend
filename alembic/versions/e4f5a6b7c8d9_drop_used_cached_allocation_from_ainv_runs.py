"""Drop used_cached_allocation from additional_investment_runs.

Dead column: hardwired False since inception — the additional-investment path
has no allocation cache (the practical allocation recomputes fresh each call),
so the column never carried information. The rebalancing twin
(RebalancingRunOutcome.used_cached_allocation) is ALIVE (real 90-day cache) and
is untouched by this migration.

DEPLOY ORDER MATTERS: apply this migration only WITH/AFTER deploying the code
that removed the column from the ORM — the older deployed backend still lists
the column in its INSERTs and would break against a dropped column. (New code
against an un-dropped column is fine: the column is nullable and unmapped
columns are ignored.)

Revision ID: e4f5a6b7c8d9
Revises: a7b8c9d0e1f2
Create Date: 2026-07-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("additional_investment_runs", "used_cached_allocation")


def downgrade() -> None:
    op.add_column(
        "additional_investment_runs",
        sa.Column("used_cached_allocation", sa.Boolean(), nullable=True),
    )
