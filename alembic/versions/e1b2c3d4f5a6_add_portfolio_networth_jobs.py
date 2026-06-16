"""Add portfolio_networth_jobs table.

Tracks the one-time net-worth-history backfill job per user (status, phase,
% progress) so the frontend can poll a real completion bar.

Revision ID: e1b2c3d4f5a6
Revises: d8e0f1a2b3c4
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1b2c3d4f5a6"
down_revision: Union[str, None] = "d8e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_networth_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "phase", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("progress_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("message", sa.String(length=300), nullable=True),
        sa.Column("history_from", sa.Date(), nullable=True),
        sa.Column("days_total", sa.Numeric(8, 0), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_portfolio_networth_jobs_user_id",
        "portfolio_networth_jobs",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_portfolio_networth_jobs_user_id",
        table_name="portfolio_networth_jobs",
    )
    op.drop_table("portfolio_networth_jobs")
