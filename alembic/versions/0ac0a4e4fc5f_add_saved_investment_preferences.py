"""saved_investment_preferences + run-table FK tagging columns (S1).

One new table — IMMUTABLE, VERSIONED preference rows (every save inserts a
new row and deactivates the prior; clear deactivates; at most one active row
per user via a partial unique index) — and one nullable
`saved_investment_preference_id` FK on each of the four run tables: the
run-level provenance pointer to exactly the values that shaped the run.
NULL = computed with no preference. No backfill: pre-existing rows are
legitimately NULL.

Revision ID: 0ac0a4e4fc5f
Revises: 9ebf3665fe81
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0ac0a4e4fc5f"
down_revision: Union[str, None] = "9ebf3665fe81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUN_TABLES = (
    "asset_allocation_runs",
    "practical_asset_allocation_runs",
    "rebalancing_runs",
    "additional_investment_runs",
)


def upgrade() -> None:
    op.create_table(
        "saved_investment_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("equity_requested_pct", sa.Float, nullable=True),
        sa.Column("debt_requested_pct", sa.Float, nullable=True),
        sa.Column("others_requested_pct", sa.Float, nullable=True),
        sa.Column("equity_target_pct", sa.Float, nullable=True),
        sa.Column("debt_target_pct", sa.Float, nullable=True),
        sa.Column("others_target_pct", sa.Float, nullable=True),
        sa.Column("resolved_targets", postgresql.JSONB, nullable=True),
        sa.Column("customer_choices", postgresql.JSONB, nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "uq_saved_investment_preferences_active_user",
        "saved_investment_preferences",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    for table in _RUN_TABLES:
        op.add_column(
            table,
            sa.Column(
                "saved_investment_preference_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "saved_investment_preferences.id", ondelete="SET NULL"
                ),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table in reversed(_RUN_TABLES):
        op.drop_column(table, "saved_investment_preference_id")
    op.drop_index(
        "uq_saved_investment_preferences_active_user",
        table_name="saved_investment_preferences",
    )
    op.drop_table("saved_investment_preferences")
