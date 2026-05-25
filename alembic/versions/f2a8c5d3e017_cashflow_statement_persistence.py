"""Cashflow-statement persistence layer.

Implements `viewer_db_schema.md`:

* Merges the two existing heads (`a1d3f5b7c901`, `d2e9f4c1a701`) so the new
  cashflow tables live on a single linear history.
* Creates the cashflow output family: `cashflow_plan_runs` and its
  cascade-deleted children (`cashflow_annual_rows`, `cashflow_monthly_rows`,
  `cashflow_headline`, `cashflow_fund_flow_summary`, `cashflow_plan_summary`).
* Creates the user-scoped cashflow input tables
  (`cashflow_input_assumptions`, `cashflow_input_one_off_events`,
  `user_current_properties`).
* Extends `personal_finance_profiles` with the `user_financial` /
  `ClientProfile` columns (merged in additively so existing onboarding rows
  remain valid).
* Extends `goals` with the cashflow-engine columns (new `goal_type_enum`,
  goal_date, *_pv/*_fv, mortgage fields, etc.) while preserving legacy
  columns for the existing onboarding/portfolio consumers.
* Adds `users.assumed_lifespan_years` (per the schema doc's "USER TABLE"
  note).

Revision ID: f2a8c5d3e017
Revises: a1d3f5b7c901, d2e9f4c1a701
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a8c5d3e017"
down_revision: Union[str, Sequence[str], None] = ("a1d3f5b7c901", "d2e9f4c1a701")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _create_enum(name: str, values: list[str]) -> None:
    """Create a PostgreSQL ENUM type if it doesn't already exist."""
    quoted = ",".join(f"'{v}'" for v in values)
    op.execute(
        f"DO $$ BEGIN "
        f"CREATE TYPE {name} AS ENUM ({quoted}); "
        f"EXCEPTION WHEN duplicate_object THEN null; "
        f"END $$;"
    )


def _drop_enum(name: str) -> None:
    op.execute(f"DROP TYPE IF EXISTS {name}")


# --------------------------------------------------------------------------- #
# Upgrade                                                                     #
# --------------------------------------------------------------------------- #


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────────────
    _create_enum(
        "goal_type_enum",
        [
            "retirement",
            "property",
            "child_abroad_education",
            "child_local_education",
            "child_marriage",
            "custom",
        ],
    )
    _create_enum("one_off_direction_enum", ["in", "out"])
    _create_enum(
        "investment_source_enum",
        ["user_sip", "user_sip_capped", "savings_sip_fraction", "withdrawal", "zero"],
    )
    _create_enum("detail_level_enum", ["default", "full"])

    # ── users: assumed_lifespan_years ────────────────────────────────────
    op.add_column(
        "users", sa.Column("assumed_lifespan_years", sa.SmallInteger(), nullable=True)
    )
    op.create_check_constraint(
        "ck_users_assumed_lifespan_years_range",
        "users",
        "assumed_lifespan_years IS NULL OR (assumed_lifespan_years BETWEEN 40 AND 130)",
    )

    # ── personal_finance_profiles: cashflow ClientProfile columns ────────
    op.add_column(
        "personal_finance_profiles",
        sa.Column("annual_income", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "personal_finance_profiles",
        sa.Column("effective_tax_rate", sa.Numeric(7, 6), nullable=True),
    )
    op.add_column(
        "personal_finance_profiles",
        sa.Column("financial_assets", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "personal_finance_profiles",
        sa.Column(
            "financial_liabilities_excl_mortgage", sa.Numeric(18, 2), nullable=True
        ),
    )
    op.add_column(
        "personal_finance_profiles",
        sa.Column("monthly_household_expense", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "personal_finance_profiles",
        sa.Column("starting_monthly_investment", sa.Numeric(15, 2), nullable=True),
    )
    for col, name in [
        ("annual_income", "ck_pfp_annual_income_nonneg"),
        ("financial_assets", "ck_pfp_financial_assets_nonneg"),
        (
            "financial_liabilities_excl_mortgage",
            "ck_pfp_financial_liabilities_nonneg",
        ),
        ("monthly_household_expense", "ck_pfp_monthly_household_expense_nonneg"),
        ("starting_monthly_investment", "ck_pfp_starting_monthly_investment_nonneg"),
    ]:
        op.create_check_constraint(
            name, "personal_finance_profiles", f"{col} IS NULL OR {col} >= 0"
        )
    op.create_check_constraint(
        "ck_pfp_effective_tax_rate_range",
        "personal_finance_profiles",
        "effective_tax_rate IS NULL OR (effective_tax_rate >= 0 AND effective_tax_rate <= 1)",
    )

    # ── goals: relax legacy NOT NULLs, add cashflow columns ──────────────
    op.alter_column("goals", "goal_name", existing_type=sa.String(100), nullable=True)
    op.alter_column(
        "goals", "present_value_amount", existing_type=sa.Numeric(15, 2), nullable=True
    )
    op.alter_column(
        "goals", "inflation_rate", existing_type=sa.Numeric(5, 2), nullable=True
    )
    op.alter_column("goals", "target_date", existing_type=sa.Date(), nullable=True)

    op.drop_constraint("ck_goals_present_value_positive", "goals", type_="check")
    op.drop_constraint("ck_goals_inflation_range", "goals", type_="check")
    op.create_check_constraint(
        "ck_goals_present_value_positive",
        "goals",
        "present_value_amount IS NULL OR present_value_amount > 0",
    )
    op.create_check_constraint(
        "ck_goals_inflation_range",
        "goals",
        "inflation_rate IS NULL OR (inflation_rate >= 0 AND inflation_rate <= 50)",
    )

    op.add_column("goals", sa.Column("name", sa.Text(), nullable=True))
    op.add_column(
        "goals",
        sa.Column(
            "goal_type_cashflow",
            postgresql.ENUM(name="goal_type_enum", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("goals", sa.Column("goal_date", sa.Date(), nullable=True))
    op.add_column("goals", sa.Column("goal_value_pv", sa.Numeric(18, 2), nullable=True))
    op.add_column("goals", sa.Column("goal_value_fv", sa.Numeric(18, 2), nullable=True))
    op.add_column("goals", sa.Column("target_pv", sa.Numeric(18, 2), nullable=True))
    op.add_column("goals", sa.Column("target_fv", sa.Numeric(18, 2), nullable=True))
    op.add_column(
        "goals",
        sa.Column(
            "is_downpayment_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("goals", sa.Column("upfront_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("goals", sa.Column("downpayment_pct", sa.Numeric(7, 6), nullable=True))
    op.add_column("goals", sa.Column("inflation_annual", sa.Numeric(7, 6), nullable=True))
    op.add_column(
        "goals", sa.Column("mortgage_tenure_years", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        "goals", sa.Column("mortgage_interest_annual", sa.Numeric(7, 6), nullable=True)
    )
    op.add_column("goals", sa.Column("date_of_birth", sa.Date(), nullable=True))

    op.create_check_constraint(
        "ck_goal_value_present",
        "goals",
        "goal_type_cashflow IS NULL "
        "OR goal_type_cashflow IN ('retirement', 'property') "
        "OR goal_value_pv IS NOT NULL OR goal_value_fv IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_property_target_present",
        "goals",
        "goal_type_cashflow IS NULL OR goal_type_cashflow <> 'property' "
        "OR target_pv IS NOT NULL OR target_fv IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_retirement_fields",
        "goals",
        "goal_type_cashflow IS NULL OR goal_type_cashflow <> 'retirement' "
        "OR (date_of_birth IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_goals_downpayment_pct_range",
        "goals",
        "downpayment_pct IS NULL OR (downpayment_pct >= 0 AND downpayment_pct <= 1)",
    )
    op.create_check_constraint(
        "ck_goals_upfront_amount_nonneg",
        "goals",
        "upfront_amount IS NULL OR upfront_amount >= 0",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_goals_name_per_user "
        "ON goals (user_id, lower(name)) WHERE name IS NOT NULL"
    )

    # ── user_current_properties ──────────────────────────────────────────
    op.create_table(
        "user_current_properties",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("property_value", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "has_mortgage", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("mortgage_emi", sa.Numeric(15, 2), nullable=True),
        sa.Column("mortgage_end_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "property_value IS NULL OR property_value >= 0",
            name="ck_user_curr_prop_value_nonneg",
        ),
        sa.CheckConstraint(
            "mortgage_emi IS NULL OR mortgage_emi >= 0",
            name="ck_user_curr_prop_emi_nonneg",
        ),
        sa.CheckConstraint(
            "has_mortgage = FALSE OR (mortgage_emi IS NOT NULL AND mortgage_end_date IS NOT NULL)",
            name="ck_mortgage_fields_present",
        ),
    )
    op.create_index(
        "ix_user_current_properties_user_id", "user_current_properties", ["user_id"]
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_user_current_properties_name "
        "ON user_current_properties (user_id, lower(name))"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_current_properties_updated_at "
        "ON user_current_properties;"
    )
    op.execute(
        "CREATE TRIGGER trg_user_current_properties_updated_at "
        "BEFORE UPDATE ON user_current_properties "
        "FOR EACH ROW EXECUTE PROCEDURE set_updated_at_timestamp();"
    )

    # ── cashflow_input_assumptions ───────────────────────────────────────
    op.create_table(
        "cashflow_input_assumptions",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("general_inflation", sa.Numeric(7, 6), nullable=False),
        sa.Column("inflation_property", sa.Numeric(7, 6), nullable=False),
        sa.Column("inflation_child_abroad_education", sa.Numeric(7, 6), nullable=False),
        sa.Column("inflation_child_local_education", sa.Numeric(7, 6), nullable=False),
        sa.Column("inflation_child_marriage", sa.Numeric(7, 6), nullable=False),
        sa.Column("inflation_household_expense", sa.Numeric(7, 6), nullable=False),
        sa.Column("inflation_post_retirement", sa.Numeric(7, 6), nullable=False),
        sa.Column("annual_income_growth", sa.Numeric(7, 6), nullable=False),
        sa.Column("annual_invested_amount_growth", sa.Numeric(7, 6), nullable=False),
        sa.Column("roi_near_term_post_tax", sa.Numeric(7, 6), nullable=False),
        sa.Column("roi_mid_term_post_tax", sa.Numeric(7, 6), nullable=False),
        sa.Column("roi_long_term_post_tax", sa.Numeric(7, 6), nullable=False),
        sa.Column("roi_retired_portfolio_annual", sa.Numeric(7, 6), nullable=False),
        sa.Column("near_term_horizon_years", sa.SmallInteger(), nullable=False),
        sa.Column("medium_term_horizon_years", sa.SmallInteger(), nullable=False),
        sa.Column("default_mortgage_tenure_years", sa.SmallInteger(), nullable=False),
        sa.Column("default_mortgage_interest_annual", sa.Numeric(7, 6), nullable=False),
        sa.Column("default_property_downpayment_pct", sa.Numeric(7, 6), nullable=False),
        sa.Column("default_sip_share", sa.Numeric(7, 6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "near_term_horizon_years >= 0", name="ck_cf_assump_near_term_nonneg"
        ),
        sa.CheckConstraint(
            "medium_term_horizon_years >= 0", name="ck_cf_assump_mid_term_nonneg"
        ),
        sa.CheckConstraint(
            "default_mortgage_tenure_years > 0",
            name="ck_cf_assump_mortgage_tenure_pos",
        ),
        sa.CheckConstraint(
            "default_property_downpayment_pct >= 0 AND default_property_downpayment_pct <= 1",
            name="ck_cf_assump_downpayment_pct_range",
        ),
        sa.CheckConstraint(
            "default_sip_share >= 0 AND default_sip_share <= 1",
            name="ck_cf_assump_sip_share_range",
        ),
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_cashflow_input_assumptions_updated_at "
        "ON cashflow_input_assumptions;"
    )
    op.execute(
        "CREATE TRIGGER trg_cashflow_input_assumptions_updated_at "
        "BEFORE UPDATE ON cashflow_input_assumptions "
        "FOR EACH ROW EXECUTE PROCEDURE set_updated_at_timestamp();"
    )

    # ── cashflow_input_one_off_events ────────────────────────────────────
    op.create_table(
        "cashflow_input_one_off_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "direction",
            postgresql.ENUM(name="one_off_direction_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_cf_one_off_amount_positive"),
    )
    op.create_index(
        "ix_cashflow_input_one_off_events_user_id",
        "cashflow_input_one_off_events",
        ["user_id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_one_off_description_per_user "
        "ON cashflow_input_one_off_events (user_id, direction, lower(description))"
    )
    op.create_index(
        "ix_one_off_events_user_date",
        "cashflow_input_one_off_events",
        ["user_id", "event_date"],
    )

    # ── cashflow_plan_runs ───────────────────────────────────────────────
    op.create_table(
        "cashflow_plan_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column(
            "detail_level",
            postgresql.ENUM(name="detail_level_enum", create_type=False),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
        sa.Column("assumption_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "warnings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_cashflow_plan_runs_user_id", "cashflow_plan_runs", ["user_id"]
    )
    op.execute(
        "CREATE INDEX ix_cashflow_plan_runs_user_id_computed_at "
        "ON cashflow_plan_runs (user_id, computed_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_cashflow_plan_runs_chat_session "
        "ON cashflow_plan_runs (chat_session_id) "
        "WHERE chat_session_id IS NOT NULL"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_cashflow_plan_runs_updated_at ON cashflow_plan_runs;"
    )
    op.execute(
        "CREATE TRIGGER trg_cashflow_plan_runs_updated_at "
        "BEFORE UPDATE ON cashflow_plan_runs "
        "FOR EACH ROW EXECUTE PROCEDURE set_updated_at_timestamp();"
    )

    # ── cashflow_annual_rows ─────────────────────────────────────────────
    op.create_table(
        "cashflow_annual_rows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fy_end_date", sa.Date(), nullable=False),
        sa.Column("fy_label", sa.String(8), nullable=False),
        sa.Column("income", sa.Numeric(18, 2), nullable=False),
        sa.Column("income_tax", sa.Numeric(18, 2), nullable=False),
        sa.Column("household_expense", sa.Numeric(18, 2), nullable=False),
        sa.Column("savings_pre_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column("existing_mortgage_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column("goal_mortgage_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column("savings_post_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "one_off_inflow",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "one_off_outflow",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "corpus_opening",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "monthly_investment",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "investment_returns",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "goal_payout",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "corpus_closing",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_funded", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.UniqueConstraint("plan_run_id", "fy_end_date", name="uq_annual_run_fy_end"),
        sa.UniqueConstraint("plan_run_id", "fy_label", name="uq_annual_run_fy_label"),
        sa.CheckConstraint("existing_mortgage_emi >= 0", name="ck_annual_existing_emi_nonneg"),
        sa.CheckConstraint("goal_mortgage_emi >= 0", name="ck_annual_goal_emi_nonneg"),
        sa.CheckConstraint("one_off_inflow >= 0", name="ck_annual_one_off_in_nonneg"),
        sa.CheckConstraint("one_off_outflow >= 0", name="ck_annual_one_off_out_nonneg"),
        sa.CheckConstraint("goal_payout >= 0", name="ck_annual_goal_payout_nonneg"),
    )
    op.create_index(
        "ix_cashflow_annual_rows_plan_run_id", "cashflow_annual_rows", ["plan_run_id"]
    )
    op.create_index(
        "ix_cashflow_annual_rows_user_id", "cashflow_annual_rows", ["user_id"]
    )

    # ── cashflow_monthly_rows ────────────────────────────────────────────
    op.create_table(
        "cashflow_monthly_rows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month_end_date", sa.Date(), nullable=False),
        sa.Column("fy_label", sa.String(8), nullable=False),
        sa.Column("income", sa.Numeric(18, 2), nullable=False),
        sa.Column("income_tax", sa.Numeric(18, 2), nullable=False),
        sa.Column("household_expense", sa.Numeric(18, 2), nullable=False),
        sa.Column("savings_pre_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column("existing_mortgage_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column("goal_mortgage_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column("savings_post_emi", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "one_off_inflow",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "one_off_outflow",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "corpus_opening",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "monthly_investment",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "investment_source",
            postgresql.ENUM(name="investment_source_enum", create_type=False),
            nullable=False,
            server_default=sa.text("'zero'"),
        ),
        sa.Column(
            "investment_returns",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "goal_payout",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "corpus_closing",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_funded", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.UniqueConstraint(
            "plan_run_id", "month_end_date", name="uq_monthly_run_month_end"
        ),
        sa.CheckConstraint(
            "month_end_date = (date_trunc('month', month_end_date) + "
            "INTERVAL '1 month - 1 day')::date",
            name="ck_month_end_date_is_eom",
        ),
        sa.CheckConstraint("existing_mortgage_emi >= 0", name="ck_monthly_existing_emi_nonneg"),
        sa.CheckConstraint("goal_mortgage_emi >= 0", name="ck_monthly_goal_emi_nonneg"),
        sa.CheckConstraint("one_off_inflow >= 0", name="ck_monthly_one_off_in_nonneg"),
        sa.CheckConstraint("one_off_outflow >= 0", name="ck_monthly_one_off_out_nonneg"),
        sa.CheckConstraint("goal_payout >= 0", name="ck_monthly_goal_payout_nonneg"),
    )
    op.create_index(
        "ix_cashflow_monthly_rows_plan_run_id", "cashflow_monthly_rows", ["plan_run_id"]
    )
    op.create_index(
        "ix_cashflow_monthly_rows_user_id", "cashflow_monthly_rows", ["user_id"]
    )

    # ── cashflow_headline ────────────────────────────────────────────────
    op.create_table(
        "cashflow_headline",
        sa.Column(
            "plan_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("years_to_last_goal", sa.SmallInteger(), nullable=False),
        sa.Column("last_goal_date", sa.Date(), nullable=False),
        sa.Column("last_fy_end_date", sa.Date(), nullable=False),
        sa.Column("number_of_goals", sa.SmallInteger(), nullable=False),
        sa.Column("corpus_today", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_corpus_required_today", sa.Numeric(18, 2), nullable=False),
        sa.Column("surplus_or_shortfall_today", sa.Numeric(18, 2), nullable=False),
        sa.Column("corpus_closing", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_shortfall_fv", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_funded_amount", sa.Numeric(18, 2), nullable=False),
        sa.CheckConstraint(
            "total_shortfall_fv >= 0", name="ck_headline_shortfall_nonneg"
        ),
        sa.CheckConstraint(
            "total_funded_amount >= 0", name="ck_headline_funded_nonneg"
        ),
    )
    op.create_index("ix_cashflow_headline_user_id", "cashflow_headline", ["user_id"])

    # ── cashflow_fund_flow_summary ───────────────────────────────────────
    op.create_table(
        "cashflow_fund_flow_summary",
        sa.Column(
            "plan_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("corpus_opening", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_investments", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_roi", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_one_off_in", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_one_off_out", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_goals_paid", sa.Numeric(18, 2), nullable=False),
        sa.Column("corpus_closing", sa.Numeric(18, 2), nullable=False),
        sa.Column("corpus_today", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_corpus_required_today", sa.Numeric(18, 2), nullable=False),
        sa.Column("surplus_or_shortfall_today", sa.Numeric(18, 2), nullable=False),
        sa.CheckConstraint("total_one_off_in >= 0", name="ck_fund_flow_one_off_in_nonneg"),
        sa.CheckConstraint("total_one_off_out >= 0", name="ck_fund_flow_one_off_out_nonneg"),
        sa.CheckConstraint("total_goals_paid >= 0", name="ck_fund_flow_goals_paid_nonneg"),
    )
    op.create_index(
        "ix_cashflow_fund_flow_summary_user_id",
        "cashflow_fund_flow_summary",
        ["user_id"],
    )

    # ── cashflow_plan_summary ────────────────────────────────────────────
    op.create_table(
        "cashflow_plan_summary",
        sa.Column(
            "plan_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("top_line", sa.Text(), nullable=False),
        sa.Column("retirement_note", sa.Text(), nullable=False),
        sa.Column("cashflow_note", sa.Text(), nullable=False),
        sa.Column("goals", postgresql.JSONB(), nullable=False),
        sa.Column("risks", postgresql.JSONB(), nullable=False),
        sa.Column("next_steps", postgresql.JSONB(), nullable=False),
        sa.Column("summary_error", sa.Text(), nullable=True),
    )


# --------------------------------------------------------------------------- #
# Downgrade                                                                   #
# --------------------------------------------------------------------------- #


def downgrade() -> None:
    # Output tables first (FK-cascade order).
    op.drop_table("cashflow_plan_summary")
    op.drop_index(
        "ix_cashflow_fund_flow_summary_user_id", table_name="cashflow_fund_flow_summary"
    )
    op.drop_table("cashflow_fund_flow_summary")
    op.drop_index("ix_cashflow_headline_user_id", table_name="cashflow_headline")
    op.drop_table("cashflow_headline")
    op.drop_index("ix_cashflow_monthly_rows_user_id", table_name="cashflow_monthly_rows")
    op.drop_index(
        "ix_cashflow_monthly_rows_plan_run_id", table_name="cashflow_monthly_rows"
    )
    op.drop_table("cashflow_monthly_rows")
    op.drop_index("ix_cashflow_annual_rows_user_id", table_name="cashflow_annual_rows")
    op.drop_index(
        "ix_cashflow_annual_rows_plan_run_id", table_name="cashflow_annual_rows"
    )
    op.drop_table("cashflow_annual_rows")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_cashflow_plan_runs_updated_at ON cashflow_plan_runs;"
    )
    op.execute("DROP INDEX IF EXISTS ix_cashflow_plan_runs_chat_session")
    op.execute("DROP INDEX IF EXISTS ix_cashflow_plan_runs_user_id_computed_at")
    op.drop_index("ix_cashflow_plan_runs_user_id", table_name="cashflow_plan_runs")
    op.drop_table("cashflow_plan_runs")

    op.drop_index(
        "ix_one_off_events_user_date", table_name="cashflow_input_one_off_events"
    )
    op.execute("DROP INDEX IF EXISTS uq_one_off_description_per_user")
    op.drop_index(
        "ix_cashflow_input_one_off_events_user_id",
        table_name="cashflow_input_one_off_events",
    )
    op.drop_table("cashflow_input_one_off_events")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_cashflow_input_assumptions_updated_at "
        "ON cashflow_input_assumptions;"
    )
    op.drop_table("cashflow_input_assumptions")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_current_properties_updated_at "
        "ON user_current_properties;"
    )
    op.execute("DROP INDEX IF EXISTS uq_user_current_properties_name")
    op.drop_index(
        "ix_user_current_properties_user_id", table_name="user_current_properties"
    )
    op.drop_table("user_current_properties")

    # goals: drop new constraints/columns and restore legacy NOT NULL state.
    op.execute("DROP INDEX IF EXISTS uq_goals_name_per_user")
    for name in (
        "ck_goals_upfront_amount_nonneg",
        "ck_goals_downpayment_pct_range",
        "ck_retirement_fields",
        "ck_property_target_present",
        "ck_goal_value_present",
    ):
        op.drop_constraint(name, "goals", type_="check")
    for col in (
        "date_of_birth",
        "mortgage_interest_annual",
        "mortgage_tenure_years",
        "inflation_annual",
        "downpayment_pct",
        "upfront_amount",
        "is_downpayment_only",
        "target_fv",
        "target_pv",
        "goal_value_fv",
        "goal_value_pv",
        "goal_date",
        "goal_type_cashflow",
        "name",
    ):
        op.drop_column("goals", col)

    op.drop_constraint("ck_goals_inflation_range", "goals", type_="check")
    op.drop_constraint("ck_goals_present_value_positive", "goals", type_="check")
    op.create_check_constraint(
        "ck_goals_present_value_positive", "goals", "present_value_amount > 0"
    )
    op.create_check_constraint(
        "ck_goals_inflation_range",
        "goals",
        "inflation_rate >= 0 AND inflation_rate <= 50",
    )

    op.alter_column("goals", "target_date", existing_type=sa.Date(), nullable=False)
    op.alter_column(
        "goals", "inflation_rate", existing_type=sa.Numeric(5, 2), nullable=False
    )
    op.alter_column(
        "goals",
        "present_value_amount",
        existing_type=sa.Numeric(15, 2),
        nullable=False,
    )
    op.alter_column("goals", "goal_name", existing_type=sa.String(100), nullable=False)

    # personal_finance_profiles
    for name in (
        "ck_pfp_starting_monthly_investment_nonneg",
        "ck_pfp_monthly_household_expense_nonneg",
        "ck_pfp_financial_liabilities_nonneg",
        "ck_pfp_financial_assets_nonneg",
        "ck_pfp_effective_tax_rate_range",
        "ck_pfp_annual_income_nonneg",
    ):
        op.drop_constraint(name, "personal_finance_profiles", type_="check")
    for col in (
        "starting_monthly_investment",
        "monthly_household_expense",
        "financial_liabilities_excl_mortgage",
        "financial_assets",
        "effective_tax_rate",
        "annual_income",
    ):
        op.drop_column("personal_finance_profiles", col)

    # users
    op.drop_constraint("ck_users_assumed_lifespan_years_range", "users", type_="check")
    op.drop_column("users", "assumed_lifespan_years")

    # Enums (must be dropped after all dependent columns/tables are gone).
    _drop_enum("detail_level_enum")
    _drop_enum("investment_source_enum")
    _drop_enum("one_off_direction_enum")
    _drop_enum("goal_type_enum")
