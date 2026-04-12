"""Fintech schema: goals v2, MF ledger, other_investments, stocks, net worth views.

Revision ID: e4f8a2b1c901
Revises: d2e91b8f7a11
Create Date: 2026-03-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4f8a2b1c901"
down_revision: Union[str, None] = "d2e91b8f7a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # â”€â”€ Enums â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _create_enum(
        "goal_type_enum",
        [
            "RETIREMENT",
            "CHILD_EDUCATION",
            "HOME_PURCHASE",
            "VEHICLE",
            "WEDDING",
            "TRAVEL",
            "EMERGENCY_FUND",
            "WEALTH_CREATION",
            "OTHER",
        ],
    )
    _create_enum("goal_priority_enum_v2", ["PRIMARY", "SECONDARY"])
    _create_enum("goal_status_enum_v2", ["ACTIVE", "ACHIEVED", "PAUSED", "ABANDONED"])
    _create_enum("mf_plan_type_enum", ["DIRECT", "REGULAR"])
    _create_enum("mf_option_type_enum", ["GROWTH", "IDCW"])
    _create_enum(
        "mf_transaction_type_enum",
        ["BUY", "SELL", "SWITCH_IN", "SWITCH_OUT", "DIVIDEND_REINVEST"],
    )
    _create_enum("mf_sip_frequency_enum", ["MONTHLY", "QUARTERLY"])
    _create_enum("mf_stepup_frequency_enum", ["ANNUALLY", "HALF_YEARLY"])
    _create_enum("mf_sip_status_enum", ["ACTIVE", "PAUSED", "CANCELLED", "COMPLETED"])
    _create_enum(
        "user_investment_list_kind_enum", ["ILLIQUID_EXIT", "STCG", "RESTRICTED"]
    )
    _create_enum("portfolio_snapshot_kind_enum", ["IDEAL", "SUGGESTED", "ACTUAL"])
    _create_enum("other_investment_status_enum", ["ACTIVE", "MATURED", "WITHDRAWN", "CLOSED"])
    _create_enum("stock_transaction_type_enum", ["BUY", "SELL"])

    # â”€â”€ MF core â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    op.create_table(
        "mf_fund_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scheme_code", sa.String(20), nullable=False),
        sa.Column("scheme_name", sa.String(200), nullable=False),
        sa.Column("amc_name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("sub_category", sa.String(100), nullable=True),
        sa.Column("plan_type", postgresql.ENUM(name="mf_plan_type_enum", create_type=False), nullable=False),
        sa.Column("option_type", postgresql.ENUM(name="mf_option_type_enum", create_type=False), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("risk_rating_sebi", sa.String(50), nullable=True),
        sa.Column("asset_class_sebi", sa.String(100), nullable=True),
        sa.Column("asset_class", sa.String(100), nullable=True),
        sa.Column("asset_subgroup", sa.String(100), nullable=True),
        sa.Column("portfolio_managers_current", sa.Text(), nullable=True),
        sa.Column("portfolio_managers_history", sa.Text(), nullable=True),
        sa.Column("portfolio_manager_change_date", sa.Date(), nullable=True),
        sa.Column("rating_external_agency_1", sa.String(50), nullable=True),
        sa.Column("rating_external_agency_2", sa.String(50), nullable=True),
        sa.Column("our_rating_parameter_1", sa.String(100), nullable=True),
        sa.Column("our_rating_parameter_2", sa.String(100), nullable=True),
        sa.Column("our_rating_parameter_3", sa.String(100), nullable=True),
        sa.Column("our_rating_history_parameter_1", sa.Text(), nullable=True),
        sa.Column("our_rating_history_parameter_2", sa.Text(), nullable=True),
        sa.Column("our_rating_history_parameter_3", sa.Text(), nullable=True),
        sa.Column("direct_plan_fees", sa.Numeric(6, 4), nullable=True),
        sa.Column("regular_plan_fees", sa.Numeric(6, 4), nullable=True),
        sa.Column("entry_load_percent", sa.Numeric(6, 4), nullable=True),
        sa.Column("exit_load_percent", sa.Numeric(6, 4), nullable=True),
        sa.Column("exit_load_months", sa.Integer(), nullable=True),
        sa.Column("large_cap_equity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("mid_cap_equity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("small_cap_equity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("debt_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("others_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("returns_1y_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("returns_3y_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("returns_5y_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("returns_10y_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("scheme_code", name="uq_mf_fund_metadata_scheme_code"),
    )
    op.execute("ALTER TABLE mf_fund_metadata ALTER COLUMN id SET DEFAULT gen_random_uuid();")
    _add_updated_at_trigger("mf_fund_metadata")

    op.create_table(
        "mf_nav_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scheme_code", sa.String(20), sa.ForeignKey("mf_fund_metadata.scheme_code", ondelete="CASCADE"), nullable=False),
        sa.Column("isin", sa.String(20), nullable=True),
        sa.Column("scheme_name", sa.String(200), nullable=False),
        sa.Column("mf_type", sa.String(200), nullable=False),
        sa.Column("nav", sa.Numeric(12, 4), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("scheme_code", "nav_date", name="uq_mf_nav_scheme_date"),
    )
    op.create_index("ix_mf_nav_history_scheme_code", "mf_nav_history", ["scheme_code"])
    op.create_index("ix_mf_nav_history_nav_date", "mf_nav_history", ["nav_date"])
    op.execute("ALTER TABLE mf_nav_history ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    op.create_table(
        "mf_sip_mandates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheme_code", sa.String(20), sa.ForeignKey("mf_fund_metadata.scheme_code", ondelete="RESTRICT"), nullable=False),
        sa.Column("folio_number", sa.String(30), nullable=True),
        sa.Column("sip_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("frequency", postgresql.ENUM(name="mf_sip_frequency_enum", create_type=False), nullable=False, server_default="MONTHLY"),
        sa.Column("debit_day", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("stepup_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("stepup_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("stepup_frequency", postgresql.ENUM(name="mf_stepup_frequency_enum", create_type=False), nullable=True),
        sa.Column("status", postgresql.ENUM(name="mf_sip_status_enum", create_type=False), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("debit_day >= 1 AND debit_day <= 28", name="ck_mf_sip_debit_day"),
        sa.CheckConstraint("sip_amount > 0", name="ck_mf_sip_amount_positive"),
    )
    op.create_index("ix_mf_sip_mandates_user_id", "mf_sip_mandates", ["user_id"])
    op.execute("ALTER TABLE mf_sip_mandates ALTER COLUMN id SET DEFAULT gen_random_uuid();")
    _add_updated_at_trigger("mf_sip_mandates")

    op.create_table(
        "mf_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheme_code", sa.String(20), sa.ForeignKey("mf_fund_metadata.scheme_code", ondelete="RESTRICT"), nullable=False),
        sa.Column("sip_mandate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mf_sip_mandates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("folio_number", sa.String(30), nullable=False),
        sa.Column("transaction_type", postgresql.ENUM(name="mf_transaction_type_enum", create_type=False), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("units", sa.Numeric(18, 4), nullable=False),
        sa.Column("nav", sa.Numeric(12, 4), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("stamp_duty", sa.Numeric(10, 2), nullable=True, server_default="0.00"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mf_transactions_user_id", "mf_transactions", ["user_id"])
    op.create_index("ix_mf_transactions_scheme_code", "mf_transactions", ["scheme_code"])
    op.create_index("ix_mf_transactions_transaction_date", "mf_transactions", ["transaction_date"])
    op.execute("ALTER TABLE mf_transactions ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    # â”€â”€ Other investments + migrate other_assets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    op.create_table(
        "other_investments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("investment_type", sa.String(50), nullable=False),
        sa.Column("investment_name", sa.String(200), nullable=False),
        sa.Column("present_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("status", postgresql.ENUM(name="other_investment_status_enum", create_type=False), nullable=False, server_default="ACTIVE"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_other_investments_user_id", "other_investments", ["user_id"])
    op.execute("ALTER TABLE other_investments ALTER COLUMN id SET DEFAULT gen_random_uuid();")
    _add_updated_at_trigger("other_investments")

    if insp.has_table("other_assets"):
        op.execute(
            """
            INSERT INTO other_investments (
              id, user_id, investment_type, investment_name, present_value, as_of_date,
              maturity_date, status, notes, created_at, updated_at
            )
            SELECT
              gen_random_uuid(),
              user_id,
              COALESCE(NULLIF(TRIM(asset_type), ''), 'OTHER'),
              asset_name,
              COALESCE(current_value, 0),
              COALESCE((created_at AT TIME ZONE 'UTC')::date, CURRENT_DATE),
              NULL,
              'ACTIVE',
              CASE WHEN details IS NOT NULL THEN details::text ELSE NULL END,
              created_at,
              updated_at
            FROM other_assets;
            """
        )
        op.drop_table("other_assets")

    # â”€â”€ Direct equity (minimal) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    op.create_table(
        "company_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("symbol", name="uq_company_metadata_symbol"),
    )
    op.create_index("ix_company_metadata_symbol", "company_metadata", ["symbol"])
    op.execute("ALTER TABLE company_metadata ALTER COLUMN id SET DEFAULT gen_random_uuid();")
    _add_updated_at_trigger("company_metadata")

    op.create_table(
        "stock_price_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(50), sa.ForeignKey("company_metadata.symbol", ondelete="CASCADE"), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("close_price", sa.Numeric(15, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("symbol", "price_date", name="uq_stock_price_symbol_date"),
    )
    op.create_index("ix_stock_price_history_price_date", "stock_price_history", ["price_date"])
    op.execute("ALTER TABLE stock_price_history ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    op.create_table(
        "stock_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(50), sa.ForeignKey("company_metadata.symbol", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_type", postgresql.ENUM(name="stock_transaction_type_enum", create_type=False), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(15, 4), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stock_transactions_user_id", "stock_transactions", ["user_id"])
    op.create_index("ix_stock_transactions_transaction_date", "stock_transactions", ["transaction_date"])
    op.execute("ALTER TABLE stock_transactions ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    # â”€â”€ Client portfolio snapshots & compliance lists â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    op.create_table(
        "user_investment_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("list_kind", postgresql.ENUM(name="user_investment_list_kind_enum", create_type=False), nullable=False),
        sa.Column("entries", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "list_kind", name="uq_user_investment_list_kind"),
    )
    op.create_index("ix_user_investment_lists_user_id", "user_investment_lists", ["user_id"])
    op.execute("ALTER TABLE user_investment_lists ALTER COLUMN id SET DEFAULT gen_random_uuid();")
    _add_updated_at_trigger("user_investment_lists")

    op.create_table(
        "portfolio_allocation_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_kind", postgresql.ENUM(name="portfolio_snapshot_kind_enum", create_type=False), nullable=False),
        sa.Column("allocation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_portfolio_alloc_snap_user", "portfolio_allocation_snapshots", ["user_id"])
    op.create_index("ix_portfolio_alloc_snap_effective", "portfolio_allocation_snapshots", ["effective_at"])
    op.execute("ALTER TABLE portfolio_allocation_snapshots ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    # â”€â”€ Goals: rename + reshape â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    insp = sa.inspect(conn)
    has_financial_goals = insp.has_table("financial_goals")
    has_goals = insp.has_table("goals")
    if has_financial_goals and not has_goals:
        op.rename_table("financial_goals", "goals")

    insp = sa.inspect(conn)
    if not insp.has_table("goals"):
        op.create_table(
            "goals",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("goal_name", sa.String(100), nullable=False),
            sa.Column("goal_type", postgresql.ENUM(name="goal_type_enum", create_type=False), nullable=False, server_default="OTHER"),
            sa.Column("present_value_amount", sa.Numeric(15, 2), nullable=False),
            sa.Column("inflation_rate", sa.Numeric(5, 2), nullable=False, server_default="6.00"),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("priority", postgresql.ENUM(name="goal_priority_enum_v2", create_type=False), nullable=False, server_default="PRIMARY"),
            sa.Column("status", postgresql.ENUM(name="goal_status_enum_v2", create_type=False), nullable=False, server_default="ACTIVE"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("present_value_amount > 0", name="ck_goals_present_value_positive"),
            sa.CheckConstraint("inflation_rate >= 0 AND inflation_rate <= 50", name="ck_goals_inflation_range"),
        )
        op.create_index("ix_goals_user_id", "goals", ["user_id"])
        op.execute("ALTER TABLE goals ALTER COLUMN id SET DEFAULT gen_random_uuid();")
        _add_updated_at_trigger("goals")
    else:
        _migrate_legacy_goals_table(conn)

    # â”€â”€ Views â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    op.execute(
        """
        CREATE OR REPLACE VIEW mf_holdings AS
        WITH tx_agg AS (
          SELECT
            user_id,
            scheme_code,
            folio_number,
            SUM(units) AS total_units,
            SUM(CASE WHEN transaction_type::text = 'BUY' THEN amount ELSE 0 END) AS invested_amount
          FROM mf_transactions
          GROUP BY user_id, scheme_code, folio_number
        ),
        latest_nav AS (
          SELECT DISTINCT ON (scheme_code)
            scheme_code,
            nav AS current_nav,
            nav_date,
            scheme_name
          FROM mf_nav_history
          ORDER BY scheme_code, nav_date DESC
        )
        SELECT
          t.user_id,
          t.scheme_code,
          COALESCE(ln.scheme_name, m.scheme_name) AS scheme_name,
          m.category,
          m.sub_category,
          m.amc_name,
          t.folio_number,
          t.total_units,
          t.invested_amount,
          ln.current_nav,
          (t.total_units * ln.current_nav) AS current_value,
          ((t.total_units * ln.current_nav) - t.invested_amount) AS unrealised_pnl,
          ln.nav_date
        FROM tx_agg t
        INNER JOIN latest_nav ln ON ln.scheme_code = t.scheme_code
        LEFT JOIN mf_fund_metadata m ON m.scheme_code = t.scheme_code
        WHERE t.total_units <> 0;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW stock_holdings AS
        WITH tx_agg AS (
          SELECT
            user_id,
            symbol,
            SUM(CASE WHEN transaction_type::text = 'BUY' THEN quantity WHEN transaction_type::text = 'SELL' THEN -quantity ELSE 0 END) AS net_qty,
            SUM(CASE WHEN transaction_type::text = 'BUY' THEN amount ELSE 0 END) AS invested_amount
          FROM stock_transactions
          GROUP BY user_id, symbol
        ),
        latest_px AS (
          SELECT DISTINCT ON (symbol)
            symbol,
            close_price AS current_price,
            price_date
          FROM stock_price_history
          ORDER BY symbol, price_date DESC
        )
        SELECT
          t.user_id,
          t.symbol,
          c.company_name,
          t.net_qty AS quantity,
          t.invested_amount,
          lp.current_price,
          (t.net_qty * lp.current_price) AS current_value,
          ((t.net_qty * lp.current_price) - t.invested_amount) AS unrealised_pnl,
          lp.price_date AS price_date
        FROM tx_agg t
        INNER JOIN company_metadata c ON c.symbol = t.symbol
        INNER JOIN latest_px lp ON lp.symbol = t.symbol
        WHERE t.net_qty <> 0;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW net_worth_summary AS
        SELECT
          u.id AS user_id,
          COALESCE(mf.mf_equity_value, 0) AS mf_equity_value,
          COALESCE(mf.mf_debt_value, 0) AS mf_debt_value,
          COALESCE(mf.mf_hybrid_value, 0) AS mf_hybrid_value,
          COALESCE(st.stock_value, 0) AS stock_value,
          COALESCE(oi.fd_rd_value, 0) AS fd_rd_value,
          COALESCE(oi.ppf_epf_value, 0) AS ppf_epf_value,
          COALESCE(oi.nps_value, 0) AS nps_value,
          COALESCE(oi.bonds_value, 0) AS bonds_value,
          COALESCE(oi.sgb_value, 0) AS sgb_value,
          COALESCE(oi.insurance_value, 0) AS insurance_value,
          COALESCE(oi.real_estate_value, 0) AS real_estate_value,
          COALESCE(mf.mf_invested, 0) + COALESCE(st.stock_invested, 0) AS total_invested,
          COALESCE(mf.mf_equity_value, 0) + COALESCE(mf.mf_debt_value, 0) + COALESCE(mf.mf_hybrid_value, 0)
            + COALESCE(st.stock_value, 0) + COALESCE(oi.fd_rd_value, 0) + COALESCE(oi.ppf_epf_value, 0)
            + COALESCE(oi.nps_value, 0) + COALESCE(oi.bonds_value, 0) + COALESCE(oi.sgb_value, 0)
            + COALESCE(oi.insurance_value, 0) + COALESCE(oi.real_estate_value, 0) AS total_current_value,
          (COALESCE(mf.mf_equity_value, 0) + COALESCE(mf.mf_debt_value, 0) + COALESCE(mf.mf_hybrid_value, 0)
            + COALESCE(st.stock_value, 0) + COALESCE(oi.fd_rd_value, 0) + COALESCE(oi.ppf_epf_value, 0)
            + COALESCE(oi.nps_value, 0) + COALESCE(oi.bonds_value, 0) + COALESCE(oi.sgb_value, 0)
            + COALESCE(oi.insurance_value, 0) + COALESCE(oi.real_estate_value, 0))
            - (COALESCE(mf.mf_invested, 0) + COALESCE(st.stock_invested, 0)) AS total_unrealised_pnl,
          (
            SELECT MAX(d)
            FROM (
              SELECT MAX(mh.nav_date) AS d FROM mf_holdings mh WHERE mh.user_id = u.id
              UNION ALL
              SELECT MAX(sh.price_date) AS d FROM stock_holdings sh WHERE sh.user_id = u.id
              UNION ALL
              SELECT MAX(oi2.as_of_date) AS d FROM other_investments oi2
                WHERE oi2.user_id = u.id AND oi2.status = 'ACTIVE'::other_investment_status_enum
            ) _dates
          ) AS last_updated
        FROM users u
        LEFT JOIN (
          SELECT user_id,
            SUM(CASE WHEN category = 'Equity' THEN current_value ELSE 0 END) AS mf_equity_value,
            SUM(CASE WHEN category = 'Debt' THEN current_value ELSE 0 END) AS mf_debt_value,
            SUM(CASE WHEN category = 'Hybrid' THEN current_value ELSE 0 END) AS mf_hybrid_value,
            SUM(invested_amount) AS mf_invested
          FROM mf_holdings
          GROUP BY user_id
        ) mf ON mf.user_id = u.id
        LEFT JOIN (
          SELECT user_id,
            SUM(current_value) AS stock_value,
            SUM(invested_amount) AS stock_invested
          FROM stock_holdings
          GROUP BY user_id
        ) st ON st.user_id = u.id
        LEFT JOIN (
          SELECT user_id,
            SUM(present_value) FILTER (WHERE investment_type IN ('FD', 'RD')) AS fd_rd_value,
            SUM(present_value) FILTER (WHERE investment_type IN ('PPF', 'EPF', 'VPF')) AS ppf_epf_value,
            SUM(present_value) FILTER (WHERE investment_type = 'NPS') AS nps_value,
            SUM(present_value) FILTER (WHERE investment_type = 'BOND') AS bonds_value,
            SUM(present_value) FILTER (WHERE investment_type = 'SGB') AS sgb_value,
            SUM(present_value) FILTER (WHERE investment_type = 'INSURANCE') AS insurance_value,
            SUM(present_value) FILTER (WHERE investment_type = 'REAL_ESTATE') AS real_estate_value
          FROM other_investments
          WHERE status = 'ACTIVE'
          GROUP BY user_id
        ) oi ON oi.user_id = u.id;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS net_worth_summary CASCADE;")
    op.execute("DROP VIEW IF EXISTS stock_holdings CASCADE;")
    op.execute("DROP VIEW IF EXISTS mf_holdings CASCADE;")

    op.drop_table("portfolio_allocation_snapshots")
    op.drop_table("user_investment_lists")
    op.drop_table("stock_transactions")
    op.drop_table("stock_price_history")
    op.drop_table("company_metadata")
    op.drop_table("mf_transactions")
    op.drop_table("mf_sip_mandates")
    op.drop_table("mf_nav_history")
    op.drop_table("mf_fund_metadata")
    op.drop_table("other_investments")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at_timestamp() CASCADE;")

    for enum_name in (
        "stock_transaction_type_enum",
        "other_investment_status_enum",
        "portfolio_snapshot_kind_enum",
        "user_investment_list_kind_enum",
        "mf_sip_status_enum",
        "mf_stepup_frequency_enum",
        "mf_sip_frequency_enum",
        "mf_transaction_type_enum",
        "mf_option_type_enum",
        "mf_plan_type_enum",
        "goal_status_enum_v2",
        "goal_priority_enum_v2",
        "goal_type_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name} CASCADE;")


def _create_enum(name: str, values: list[str]) -> None:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    op.execute(f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({quoted}); EXCEPTION WHEN duplicate_object THEN null; END $$;")


def _add_updated_at_trigger(table: str) -> None:
    trg = f"trg_{table}_updated_at"
    op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {table};")
    op.execute(
        f"""
        CREATE TRIGGER {trg}
        BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE PROCEDURE set_updated_at_timestamp
        """
    )


def _migrate_legacy_goals_table(conn) -> None:
    """Alter existing `goals` table from legacy financial_goals shape."""
    insp = sa.inspect(conn)
    if not insp.has_table("goals"):
        return
    cols = {c["name"] for c in insp.get_columns("goals")}
    if {"goal_name", "goal_type", "present_value_amount"}.issubset(cols):
        _add_updated_at_trigger("goals")
        return

    if "name" in cols and "goal_name" not in cols:
        op.alter_column(
            "goals",
            "name",
            new_column_name="goal_name",
            existing_type=sa.String(255),
            type_=sa.String(100),
        )
    if "target_amount" in cols and "present_value_amount" not in cols:
        op.alter_column("goals", "target_amount", new_column_name="present_value_amount")

    if "goal_type" not in cols:
        op.add_column(
            "goals",
            sa.Column(
                "goal_type",
                postgresql.ENUM(name="goal_type_enum", create_type=False),
                server_default="OTHER",
                nullable=True,
            ),
        )
        op.execute("UPDATE goals SET goal_type = 'OTHER' WHERE goal_type IS NULL;")
        op.alter_column("goals", "goal_type", nullable=False)

    if "inflation_rate" not in cols:
        op.add_column(
            "goals",
            sa.Column("inflation_rate", sa.Numeric(5, 2), server_default="6.00", nullable=True),
        )
        op.execute("UPDATE goals SET inflation_rate = 6.00 WHERE inflation_rate IS NULL;")
        op.alter_column("goals", "inflation_rate", nullable=False)

    if "notes" not in cols:
        op.add_column("goals", sa.Column("notes", sa.Text(), nullable=True))
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("goals")}
    if "description" in cols:
        op.execute(
            "UPDATE goals SET notes = COALESCE(notes, description) WHERE description IS NOT NULL;"
        )

    op.execute("UPDATE goals SET target_date = DATE '2099-12-31' WHERE target_date IS NULL;")
    op.alter_column("goals", "target_date", existing_type=sa.Date(), nullable=False)

    op.execute(
        """
        UPDATE goals SET present_value_amount = 1
        WHERE present_value_amount IS NULL OR present_value_amount <= 0;
        """
    )

    cols = {c["name"] for c in insp.get_columns("goals")}
    if "priority" in cols:
        pri_col = next(c for c in insp.get_columns("goals") if c["name"] == "priority")
        if "goal_priority_enum_v2" not in str(pri_col["type"]):
            op.add_column("goals", sa.Column("priority_new", sa.String(20), nullable=True))
            op.execute(
                """
                UPDATE goals SET priority_new = CASE
                  WHEN priority::text = 'low' THEN 'SECONDARY'
                  ELSE 'PRIMARY'
                END;
                """
            )
            op.drop_column("goals", "priority")
            op.alter_column(
                "goals", "priority_new", new_column_name="priority", existing_type=sa.String(20)
            )
            op.execute(
                "ALTER TABLE goals ALTER COLUMN priority TYPE goal_priority_enum_v2 "
                "USING priority::goal_priority_enum_v2;"
            )

    cols = {c["name"] for c in insp.get_columns("goals")}
    if "status" in cols:
        status_col = next(c for c in insp.get_columns("goals") if c["name"] == "status")
        type_str = str(status_col["type"])
        if "goal_status_enum_v2" not in type_str:
            op.add_column("goals", sa.Column("status_new", sa.String(30), nullable=True))
            op.execute(
                """
                UPDATE goals SET status_new = CASE
                  WHEN status::text = 'achieved' THEN 'ACHIEVED'
                  WHEN status::text = 'paused' THEN 'PAUSED'
                  WHEN status::text = 'cancelled' THEN 'ABANDONED'
                  ELSE 'ACTIVE'
                END;
                """
            )
            op.drop_column("goals", "status")
            op.alter_column("goals", "status_new", new_column_name="status", existing_type=sa.String(30))
            op.execute(
                "ALTER TABLE goals ALTER COLUMN status TYPE goal_status_enum_v2 USING status::goal_status_enum_v2;"
            )

    for col in (
        "slug",
        "icon",
        "description",
        "invested_amount",
        "current_value",
        "monthly_contribution",
        "suggested_contribution",
    ):
        names = {c["name"] for c in sa.inspect(conn).get_columns("goals")}
        if col in names:
            op.drop_column("goals", col)

    insp = sa.inspect(conn)
    chk = {c["name"] for c in insp.get_check_constraints("goals")}
    if "ck_goals_present_value_positive" not in chk:
        op.create_check_constraint(
            "ck_goals_present_value_positive", "goals", "present_value_amount > 0"
        )
    if "ck_goals_inflation_range" not in chk:
        op.create_check_constraint(
            "ck_goals_inflation_range", "goals", "inflation_rate >= 0 AND inflation_rate <= 50"
        )

    op.execute("DROP TYPE IF EXISTS goal_priority_enum CASCADE;")
    op.execute("DROP TYPE IF EXISTS goal_status_enum CASCADE;")

    _add_updated_at_trigger("goals")
