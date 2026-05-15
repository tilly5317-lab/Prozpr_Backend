"""Add cas_type to mf_aa_imports.

Records whether an uploaded CAMS / KFintech statement was a SUMMARY (holdings
only, no transaction rows) or a DETAILED statement — so a later "why are there
no transactions?" question is answerable without re-uploading the PDF.

Revision ID: a1d3f5b7c901
Revises: f9a0b1c2d3e4
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1d3f5b7c901"
down_revision: Union[str, None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mf_aa_imports", sa.Column("cas_type", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("mf_aa_imports", "cas_type")
