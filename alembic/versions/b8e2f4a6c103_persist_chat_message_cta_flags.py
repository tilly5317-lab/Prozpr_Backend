"""persist the per-message CTA on chat_messages

Which control a reply offered rode the send-response envelope only, so
"Open preferences" and the "Add CAMS statement" card lived exactly as long as
the chat screen stayed mounted. Reopening a session rebuilds the conversation
from these rows, which carried no CTA state, so the control vanished while the
reply's own copy still said "tap below".

ONE nullable column, not one flag per control: a turn raises at most one CTA
(the no-holdings gate returns before any flow runs, so `add_cams` and
`preferences` cannot co-occur), and a future CTA is then a new value rather than
another migration. NULL — the default for every existing row — means no control,
which is what they rendered anyway.

It cannot ride the existing `intent` tag: a turn can carry a real plan AND a
CTA, and the client reads `intent` as a fetch gate for that plan, so overwriting
it would drop "View plan" / "Save plan" for the whole session.

On the shared dev RDS this also lands via targeted DDL (alembic_version points
at an unapplied revision), applied alongside this migration.

Revision ID: b8e2f4a6c103
Revises: 035b65ba0b53
Create Date: 2026-09-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8e2f4a6c103"
down_revision: Union[str, None] = "035b65ba0b53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("cta", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "cta")
