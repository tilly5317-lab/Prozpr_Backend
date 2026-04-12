"""add risk_capacity investment_experience liquidity_needs income_needs

Revision ID: 998b6998b6aa
Revises: 
Create Date: 2026-03-21 11:37:31.323453
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '998b6998b6aa'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE investment_profiles ADD COLUMN IF NOT EXISTS liquidity_needs TEXT")
    op.execute(
        "ALTER TABLE investment_profiles ADD COLUMN IF NOT EXISTS income_needs NUMERIC(15, 2)"
    )
    op.execute("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS risk_capacity VARCHAR(50)")
    op.execute(
        "ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS investment_experience VARCHAR(100)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE risk_profiles DROP COLUMN IF EXISTS investment_experience")
    op.execute("ALTER TABLE risk_profiles DROP COLUMN IF EXISTS risk_capacity")
    op.execute("ALTER TABLE investment_profiles DROP COLUMN IF EXISTS income_needs")
    op.execute("ALTER TABLE investment_profiles DROP COLUMN IF EXISTS liquidity_needs")
