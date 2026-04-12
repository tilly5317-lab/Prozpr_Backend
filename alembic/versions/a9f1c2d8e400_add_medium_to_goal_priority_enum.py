"""Add MEDIUM to goal_priority_enum_v2 for three-level UI priority."""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "a9f1c2d8e400"
down_revision: Union[str, None] = "f7c91d2e4a00"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE goal_priority_enum_v2 ADD VALUE IF NOT EXISTS 'MEDIUM'")


def downgrade() -> None:
    # PostgreSQL cannot drop enum values safely; leave MEDIUM in place.
    pass
