"""add family_members table

Revision ID: c3a7f2e1d456
Revises: b8b16d9c1ccf
Create Date: 2026-03-23 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3a7f2e1d456"
down_revision: Union[str, None] = "b8b16d9c1ccf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "family_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "member_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("nickname", sa.String(120), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("relationship_type", sa.String(30), nullable=False, server_default="other"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_otp"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "member_user_id", name="uq_owner_member"),
    )


def downgrade() -> None:
    op.drop_table("family_members")
