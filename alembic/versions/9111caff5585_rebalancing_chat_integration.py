"""rebalancing_chat_integration

Revision ID: 9111caff5585
Revises: ee8987d840c5
Create Date: 2026-04-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "9111caff5585"
down_revision: Union[str, None] = "ee8987d840c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. recommendation_type enum + column on rebalancing_recommendations.
    rec_type = sa.Enum(
        "allocation", "rebalancing_trades",
        name="recommendation_type_enum",
        create_constraint=True,
    )
    rec_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rebalancing_recommendations",
        sa.Column(
            "recommendation_type",
            rec_type,
            nullable=False,
            server_default="allocation",
        ),
    )
    op.create_index(
        "ix_rebrec_recommendation_type",
        "rebalancing_recommendations",
        ["recommendation_type"],
    )
    # Drop server_default after backfill so future inserts must set it explicitly.
    op.alter_column(
        "rebalancing_recommendations", "recommendation_type", server_default=None
    )

    # 2. self-FK source_allocation_id on rebalancing_recommendations.
    op.add_column(
        "rebalancing_recommendations",
        sa.Column(
            "source_allocation_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_rebrec_source_allocation",
        "rebalancing_recommendations",
        "rebalancing_recommendations",
        ["source_allocation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. tax_profiles new columns.
    op.add_column(
        "tax_profiles", sa.Column("tax_regime", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "tax_profiles",
        sa.Column(
            "carryforward_st_loss_inr",
            sa.Numeric(15, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "tax_profiles",
        sa.Column(
            "carryforward_lt_loss_inr",
            sa.Numeric(15, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("tax_profiles", "carryforward_lt_loss_inr")
    op.drop_column("tax_profiles", "carryforward_st_loss_inr")
    op.drop_column("tax_profiles", "tax_regime")

    op.drop_constraint(
        "fk_rebrec_source_allocation",
        "rebalancing_recommendations",
        type_="foreignkey",
    )
    op.drop_column("rebalancing_recommendations", "source_allocation_id")

    op.drop_index(
        "ix_rebrec_recommendation_type", table_name="rebalancing_recommendations"
    )
    op.drop_column("rebalancing_recommendations", "recommendation_type")
    sa.Enum(name="recommendation_type_enum").drop(op.get_bind(), checkfirst=True)
