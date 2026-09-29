"""add origin to additional_investment_runs

Candidate/plain provenance tag mirroring rebalancing_runs.origin (revision
9ebf3665fe81). Nullable + indexed VARCHAR(16): NULL = a plain committed deploy,
"candidate" = an unsaved what-if the customer previewed. The Invest-page SIP /
lump-sum reads serve NON-candidate runs only, so an unsaved what-if no longer
leaks as the newest plan. (AINV has no "saved" run state — Save activates the
preference row, and the eager refresh writes a fresh plain run.)

On the shared dev RDS this also lands via targeted DDL (alembic_version points at
an unapplied revision), applied alongside this migration.

Revision ID: 035b65ba0b53
Revises: c9d3e5f7a1b2
Create Date: 2026-09-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "035b65ba0b53"
down_revision: Union[str, None] = "c9d3e5f7a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "additional_investment_runs",
        sa.Column("origin", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "ix_additional_investment_runs_origin",
        "additional_investment_runs",
        ["origin"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_additional_investment_runs_origin",
        table_name="additional_investment_runs",
    )
    op.drop_column("additional_investment_runs", "origin")
