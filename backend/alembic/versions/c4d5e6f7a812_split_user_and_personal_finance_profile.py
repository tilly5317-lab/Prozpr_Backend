"""Split non-financial user data into users and rename profile table."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d5e6f7a812"
down_revision: Union[str, None] = "b1c2d3e4f501"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("middle_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("pan", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("occupation", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("family_status", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column(
        "users",
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="GBP"),
    )
    op.create_index("ix_users_pan", "users", ["pan"], unique=True)

    op.create_table(
        "personal_finance_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_goals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("custom_goals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("investment_horizon", sa.String(length=50), nullable=True),
        sa.Column("annual_income_min", sa.Numeric(15, 2), nullable=True),
        sa.Column("annual_income_max", sa.Numeric(15, 2), nullable=True),
        sa.Column("annual_expense_min", sa.Numeric(15, 2), nullable=True),
        sa.Column("annual_expense_max", sa.Numeric(15, 2), nullable=True),
        sa.Column("wealth_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("personal_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_personal_finance_profiles_user_id"),
    )

    op.execute(
        """
        UPDATE users AS u
        SET
            date_of_birth = up.date_of_birth,
            occupation = up.occupation,
            family_status = up.family_status,
            address = up.address,
            currency = COALESCE(up.currency, u.currency)
        FROM user_profiles AS up
        WHERE up.user_id = u.id
        """
    )

    op.execute(
        """
        INSERT INTO personal_finance_profiles (
            id, user_id, selected_goals, custom_goals, investment_horizon,
            annual_income_min, annual_income_max, annual_expense_min, annual_expense_max,
            wealth_sources, personal_values, created_at, updated_at
        )
        SELECT
            up.id, up.user_id, up.selected_goals, up.custom_goals, up.investment_horizon,
            up.annual_income_min, up.annual_income_max, up.annual_expense_min, up.annual_expense_max,
            up.wealth_sources, up.personal_values, up.created_at, up.updated_at
        FROM user_profiles AS up
        """
    )

    op.drop_table("user_profiles")
    op.alter_column("users", "currency", server_default=None)


def downgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("selected_goals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("custom_goals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("investment_horizon", sa.String(length=50), nullable=True),
        sa.Column("annual_income_min", sa.Numeric(15, 2), nullable=True),
        sa.Column("annual_income_max", sa.Numeric(15, 2), nullable=True),
        sa.Column("annual_expense_min", sa.Numeric(15, 2), nullable=True),
        sa.Column("annual_expense_max", sa.Numeric(15, 2), nullable=True),
        sa.Column("occupation", sa.String(length=100), nullable=True),
        sa.Column("family_status", sa.String(length=100), nullable=True),
        sa.Column("wealth_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("personal_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="GBP"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
    )

    op.execute(
        """
        INSERT INTO user_profiles (
            id, user_id, date_of_birth, selected_goals, custom_goals, investment_horizon,
            annual_income_min, annual_income_max, annual_expense_min, annual_expense_max,
            occupation, family_status, wealth_sources, personal_values, address, currency,
            created_at, updated_at
        )
        SELECT
            pfp.id, pfp.user_id, u.date_of_birth, pfp.selected_goals, pfp.custom_goals, pfp.investment_horizon,
            pfp.annual_income_min, pfp.annual_income_max, pfp.annual_expense_min, pfp.annual_expense_max,
            u.occupation, u.family_status, pfp.wealth_sources, pfp.personal_values, u.address, u.currency,
            pfp.created_at, pfp.updated_at
        FROM personal_finance_profiles AS pfp
        JOIN users AS u ON u.id = pfp.user_id
        """
    )

    op.drop_table("personal_finance_profiles")
    op.drop_index("ix_users_pan", table_name="users")
    op.drop_column("users", "currency")
    op.drop_column("users", "address")
    op.drop_column("users", "family_status")
    op.drop_column("users", "occupation")
    op.drop_column("users", "date_of_birth")
    op.drop_column("users", "pan")
    op.drop_column("users", "middle_name")
