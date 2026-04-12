"""add user_id to funds

Revision ID: d2e91b8f7a11
Revises: c3a7f2e1d456
Create Date: 2026-03-23 22:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d2e91b8f7a11"
down_revision: Union[str, None] = "c3a7f2e1d456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("funds", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f("ix_funds_user_id"), "funds", ["user_id"], unique=False)
    op.create_foreign_key(
        "fk_funds_user_id_users",
        "funds",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_funds_user_id_users", "funds", type_="foreignkey")
    op.drop_index(op.f("ix_funds_user_id"), table_name="funds")
    op.drop_column("funds", "user_id")

