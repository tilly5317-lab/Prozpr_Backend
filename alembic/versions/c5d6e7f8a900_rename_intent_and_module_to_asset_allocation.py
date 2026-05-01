"""Rename portfolio_optimisation intent and goal_based_allocation module to asset_allocation.

Companion to the source-tree rename that unifies terminology so the intent label,
module identifier, and engine package all use ``asset_allocation``.

Revision ID: c5d6e7f8a900
Revises: a1b2c3d4e500
Create Date: 2026-05-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "c5d6e7f8a900"
down_revision: Union[str, None] = "a1b2c3d4e500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE chat_ai_module_runs SET module = 'asset_allocation' "
        "WHERE module = 'goal_based_allocation'"
    )
    op.execute(
        "UPDATE chat_ai_module_runs SET intent_detected = 'asset_allocation' "
        "WHERE intent_detected = 'portfolio_optimisation'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE chat_ai_module_runs SET intent_detected = 'portfolio_optimisation' "
        "WHERE intent_detected = 'asset_allocation'"
    )
    op.execute(
        "UPDATE chat_ai_module_runs SET module = 'goal_based_allocation' "
        "WHERE module = 'asset_allocation'"
    )
