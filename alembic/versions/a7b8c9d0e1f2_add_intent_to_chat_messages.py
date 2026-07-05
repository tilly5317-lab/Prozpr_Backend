"""Add intent to chat_messages.

Tags each ASSISTANT message with the turn's classified intent at write time
(NULL on user rows and on historical rows). Replaces the intent classifier's
string-prefix matching of canned-redirect replies — prefixes broke silently on
every copy tone-refresh and never matched the LLM-tailored redirects at all.
Old rows keep the legacy prefix matching as a frozen fallback.

The column is nullable and additive, so this is backward-compatible.

Revision ID: a7b8c9d0e1f2
Revises: c1d2e3f4a5b6
Create Date: 2026-07-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("intent", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "intent")
