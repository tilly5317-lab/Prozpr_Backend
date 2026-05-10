"""Replace JSONB-blob allocation/rebalancing tables with normalized families.

Drops the previous mix (``goal_allocation_recommendations``,
``rebalancing_recommendations``, ``rebalancing_recommendation_summaries``,
``rebalancing_bucket_recommendations``, ``rebalancing_bucket_goals``,
``rebalancing_bucket_subgroup_allocations``, ``rebalancing_asset_class_breakdowns``,
``rebalancing_future_investments``) and creates two clean families:

- ``goal_allocation_*``    — output of the goal-based allocation pipeline.
- ``rebalancing_*``        — output of the rebalancing engine.

Also merges the two prior heads (``b7e9c4f01a23`` and ``c9d0e1f2a3b4``)
that were running in parallel branches.

Revision ID: d6e7f8a90b12
Revises: b7e9c4f01a23, c9d0e1f2a3b4
Create Date: 2026-05-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d6e7f8a90b12"
down_revision: Union[str, Sequence[str], None] = ("b7e9c4f01a23", "c9d0e1f2a3b4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Enum definitions (created once; reused on every table that needs them) ──

GOAL_ALLOCATION_RUN_STATUS = postgresql.ENUM(
    "pending", "approved", "superseded", "rejected",
    name="goal_allocation_run_status_enum",
    create_type=False,
)
ALLOCATION_BUCKET_NAME = postgresql.ENUM(
    "emergency", "short_term", "medium_term", "long_term",
    name="allocation_bucket_name_enum",
    create_type=False,
)
ASSET_CLASS_SPLIT_KIND = postgresql.ENUM(
    "planned", "actual",
    name="asset_class_split_kind_enum",
    create_type=False,
)
REBALANCING_RUN_STATUS = postgresql.ENUM(
    "pending", "approved", "executed", "rejected",
    name="rebalancing_run_status_enum",
    create_type=False,
)
TAX_REGIME = postgresql.ENUM(
    "old", "new",
    name="rebalancing_tax_regime_enum",
    create_type=False,
)
TRADE_ACTION = postgresql.ENUM(
    "BUY", "SELL", "EXIT",
    name="rebalancing_trade_action_enum",
    create_type=False,
)
TRADE_EXECUTION_STATUS = postgresql.ENUM(
    "pending", "executed", "skipped", "failed",
    name="rebalancing_trade_execution_status_enum",
    create_type=False,
)
WARNING_CODE = postgresql.ENUM(
    "UNREBALANCED_REMAINDER", "BAD_FUND_DETECTED",
    "STCG_BUDGET_BINDING", "NO_HOLDINGS_FOR_RECOMMENDED_FUND",
    name="rebalancing_warning_code_enum",
    create_type=False,
)


# Old enums that we'll drop on upgrade and recreate on downgrade.
OLD_RECOMMENDATION_TYPE = sa.Enum(
    "allocation", "rebalancing_trades",
    name="recommendation_type_enum",
    create_constraint=True,
)
OLD_REBALANCING_STATUS = sa.Enum(
    "pending", "approved", "executed", "rejected",
    name="rebalancing_status_enum",
    create_constraint=True,
)


def upgrade() -> None:
    # 1. Drop old derived tables (must come before parent rebalancing_recommendations).
    # Some environments never had these tables created (they predate the current
    # alembic chain), so use IF EXISTS to keep the upgrade idempotent.
    for _stale in (
        "rebalancing_future_investments",
        "rebalancing_asset_class_breakdowns",
        "rebalancing_bucket_subgroup_allocations",
        "rebalancing_bucket_goals",
        "rebalancing_bucket_recommendations",
        "rebalancing_recommendation_summaries",
        "goal_allocation_recommendations",
        "rebalancing_recommendations",
    ):
        op.execute(f'DROP TABLE IF EXISTS "{_stale}" CASCADE')

    # 3. Drop old enums no longer needed.
    OLD_RECOMMENDATION_TYPE.drop(op.get_bind(), checkfirst=True)
    OLD_REBALANCING_STATUS.drop(op.get_bind(), checkfirst=True)

    # 4. Create new enums.
    bind = op.get_bind()
    GOAL_ALLOCATION_RUN_STATUS.create(bind, checkfirst=True)
    ALLOCATION_BUCKET_NAME.create(bind, checkfirst=True)
    ASSET_CLASS_SPLIT_KIND.create(bind, checkfirst=True)
    REBALANCING_RUN_STATUS.create(bind, checkfirst=True)
    TAX_REGIME.create(bind, checkfirst=True)
    TRADE_ACTION.create(bind, checkfirst=True)
    TRADE_EXECUTION_STATUS.create(bind, checkfirst=True)
    WARNING_CODE.create(bind, checkfirst=True)

    # 5. goal_allocation_runs.
    op.create_table(
        "goal_allocation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            GOAL_ALLOCATION_RUN_STATUS,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "pipeline_source",
            sa.String(80),
            nullable=False,
            server_default="asset_allocation_pydantic",
        ),
        sa.Column("spine_mode", sa.String(80), nullable=True),
        sa.Column("user_question", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("client_age", sa.Integer(), nullable=False),
        sa.Column("client_occupation", sa.String(80), nullable=True),
        sa.Column("client_effective_risk_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("total_corpus", sa.Numeric(18, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("equity_total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("debt_total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("others_total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("equity_total_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("debt_total_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("others_total_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column(
            "all_amounts_in_multiples_of_100",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_goal_allocation_runs_user_id", "goal_allocation_runs", ["user_id"])
    op.create_index("ix_goal_allocation_runs_portfolio_id", "goal_allocation_runs", ["portfolio_id"])
    op.create_index("ix_goal_allocation_runs_chat_session_id", "goal_allocation_runs", ["chat_session_id"])

    # 6. goal_allocation_goals.
    op.create_table(
        "goal_allocation_goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "financial_goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("goal_name", sa.String(150), nullable=False),
        sa.Column("time_to_goal_months", sa.Integer(), nullable=False),
        sa.Column("amount_needed", sa.Numeric(18, 2), nullable=False),
        sa.Column("goal_priority", sa.String(40), nullable=False),
        sa.Column(
            "investment_goal",
            sa.String(60),
            nullable=False,
            server_default="wealth_creation",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_goal_allocation_goals_run_id", "goal_allocation_goals", ["run_id"])
    op.create_index("ix_goal_allocation_goals_financial_goal_id", "goal_allocation_goals", ["financial_goal_id"])

    # 7. goal_allocation_buckets.
    op.create_table(
        "goal_allocation_buckets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket_name", ALLOCATION_BUCKET_NAME, nullable=False),
        sa.Column("total_goal_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("allocated_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "future_investment_amount",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("future_investment_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "bucket_name", name="uq_goal_allocation_buckets_run_bucket"),
    )
    op.create_index("ix_goal_allocation_buckets_run_id", "goal_allocation_buckets", ["run_id"])

    # 8. goal_allocation_bucket_goals.
    op.create_table(
        "goal_allocation_bucket_goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_buckets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal_rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("bucket_id", "goal_id", name="uq_goal_allocation_bucket_goals_bucket_goal"),
    )
    op.create_index("ix_goal_allocation_bucket_goals_bucket_id", "goal_allocation_bucket_goals", ["bucket_id"])
    op.create_index("ix_goal_allocation_bucket_goals_goal_id", "goal_allocation_bucket_goals", ["goal_id"])

    # 9. goal_allocation_bucket_subgroups.
    op.create_table(
        "goal_allocation_bucket_subgroups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_buckets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subgroup", sa.String(80), nullable=False),
        sa.Column("planned_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("actual_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("planned_pct_of_bucket", sa.Numeric(7, 2), nullable=True),
        sa.Column("actual_pct_of_bucket", sa.Numeric(7, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("bucket_id", "subgroup", name="uq_goal_allocation_bucket_subgroups_bucket_subgroup"),
    )
    op.create_index("ix_goal_allocation_bucket_subgroups_bucket_id", "goal_allocation_bucket_subgroups", ["bucket_id"])

    # 10. goal_allocation_bucket_asset_classes.
    op.create_table(
        "goal_allocation_bucket_asset_classes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_buckets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("split_kind", ASSET_CLASS_SPLIT_KIND, nullable=False),
        sa.Column("equity_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("debt_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("others_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("equity_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("debt_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("others_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("bucket_id", "split_kind", name="uq_goal_allocation_bucket_asset_classes_bucket_kind"),
    )
    op.create_index(
        "ix_goal_allocation_bucket_asset_classes_bucket_id",
        "goal_allocation_bucket_asset_classes",
        ["bucket_id"],
    )

    # 11. rebalancing_runs.
    op.create_table(
        "rebalancing_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_allocation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_allocation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", REBALANCING_RUN_STATUS, nullable=False, server_default="pending"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("engine_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tax_regime", TAX_REGIME, nullable=False),
        sa.Column("effective_tax_rate_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("total_corpus", sa.Numeric(18, 2), nullable=False),
        sa.Column("rounding_step", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("stcg_offset_budget_inr", sa.Numeric(18, 2), nullable=True),
        sa.Column("carryforward_st_loss_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("carryforward_lt_loss_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("knob_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("used_cached_allocation", sa.Boolean(), nullable=True),
        sa.Column("user_question", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rebalancing_runs_user_id", "rebalancing_runs", ["user_id"])
    op.create_index("ix_rebalancing_runs_portfolio_id", "rebalancing_runs", ["portfolio_id"])
    op.create_index("ix_rebalancing_runs_chat_session_id", "rebalancing_runs", ["chat_session_id"])
    op.create_index("ix_rebalancing_runs_source_allocation_run_id", "rebalancing_runs", ["source_allocation_run_id"])

    # 12. rebalancing_totals.
    op.create_table(
        "rebalancing_totals",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("total_buy_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_sell_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("net_cash_flow_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_stcg_realised", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_ltcg_realised", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_stcg_net_off", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_tax_estimate_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_exit_load_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("unrebalanced_remainder_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("rows_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("funds_to_buy_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("funds_to_sell_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("funds_to_exit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("funds_held_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # 13. rebalancing_subgroup_summaries.
    op.create_table(
        "rebalancing_subgroup_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_subgroup", sa.String(80), nullable=False),
        sa.Column("goal_target_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("current_holding_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("suggested_final_holding_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("rebalance_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_buy_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_sell_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("ranks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ranks_with_holding", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ranks_with_action", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "asset_subgroup", name="uq_rebalancing_subgroup_summaries_run_subgroup"),
    )
    op.create_index("ix_rebalancing_subgroup_summaries_run_id", "rebalancing_subgroup_summaries", ["run_id"])

    # 14. rebalancing_fund_rows.
    op.create_table(
        "rebalancing_fund_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("isin", sa.String(20), nullable=False),
        sa.Column("recommended_fund", sa.String(255), nullable=False),
        sa.Column("asset_subgroup", sa.String(80), nullable=False),
        sa.Column("sub_category", sa.String(80), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("fund_rating", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("target_amount_pre_cap", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("max_pct", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("target_pre_cap_pct", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("target_own_capped_pct", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("final_target_pct", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("final_target_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("present_allocation_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("invested_cost_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("st_value_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("st_cost_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("lt_value_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("lt_cost_inr", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("exit_load_pct", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("exit_load_months", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units_within_exit_load_period", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("current_nav", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("exit_load_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("diff", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("exit_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("worth_to_change", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stcg_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("ltcg_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_buy_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_underbuy_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_sell_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_undersell_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_sell_lt_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_realised_ltcg", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_sell_st_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_realised_stcg", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("stcg_budget_remaining_after_pass1", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_sell_amount_no_stcg_cap", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_undersell_due_to_stcg_cap", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass1_blocked_stcg_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("holding_after_initial_trades", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("stcg_offset_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass2_sell_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pass2_undersell_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("final_holding_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "isin", "rank", name="uq_rebalancing_fund_rows_run_isin_rank"),
    )
    op.create_index("ix_rebalancing_fund_rows_run_id", "rebalancing_fund_rows", ["run_id"])
    op.create_index(
        "ix_rebalancing_fund_rows_run_subgroup",
        "rebalancing_fund_rows",
        ["run_id", "asset_subgroup"],
    )

    # 15. rebalancing_trades.
    op.create_table(
        "rebalancing_trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("isin", sa.String(20), nullable=False),
        sa.Column("recommended_fund", sa.String(255), nullable=False),
        sa.Column("asset_subgroup", sa.String(80), nullable=False),
        sa.Column("sub_category", sa.String(80), nullable=False),
        sa.Column("action", TRADE_ACTION, nullable=False),
        sa.Column("amount_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("reason_title", sa.String(160), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("execution_status", TRADE_EXECUTION_STATUS, nullable=False, server_default="pending"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker_ref", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rebalancing_trades_run_id", "rebalancing_trades", ["run_id"])
    op.create_index("ix_rebalancing_trades_run_action", "rebalancing_trades", ["run_id", "action"])

    # 16. rebalancing_warnings.
    op.create_table(
        "rebalancing_warnings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", WARNING_CODE, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "affected_isins",
            postgresql.ARRAY(sa.String(20)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rebalancing_warnings_run_id", "rebalancing_warnings", ["run_id"])
    op.create_index("ix_rebalancing_warnings_code", "rebalancing_warnings", ["code"])


def downgrade() -> None:
    # Drop new tables in reverse dependency order.
    op.drop_index("ix_rebalancing_warnings_code", table_name="rebalancing_warnings")
    op.drop_index("ix_rebalancing_warnings_run_id", table_name="rebalancing_warnings")
    op.drop_table("rebalancing_warnings")

    op.drop_index("ix_rebalancing_trades_run_action", table_name="rebalancing_trades")
    op.drop_index("ix_rebalancing_trades_run_id", table_name="rebalancing_trades")
    op.drop_table("rebalancing_trades")

    op.drop_index("ix_rebalancing_fund_rows_run_subgroup", table_name="rebalancing_fund_rows")
    op.drop_index("ix_rebalancing_fund_rows_run_id", table_name="rebalancing_fund_rows")
    op.drop_table("rebalancing_fund_rows")

    op.drop_index("ix_rebalancing_subgroup_summaries_run_id", table_name="rebalancing_subgroup_summaries")
    op.drop_table("rebalancing_subgroup_summaries")

    op.drop_table("rebalancing_totals")

    op.drop_index("ix_rebalancing_runs_source_allocation_run_id", table_name="rebalancing_runs")
    op.drop_index("ix_rebalancing_runs_chat_session_id", table_name="rebalancing_runs")
    op.drop_index("ix_rebalancing_runs_portfolio_id", table_name="rebalancing_runs")
    op.drop_index("ix_rebalancing_runs_user_id", table_name="rebalancing_runs")
    op.drop_table("rebalancing_runs")

    op.drop_index(
        "ix_goal_allocation_bucket_asset_classes_bucket_id",
        table_name="goal_allocation_bucket_asset_classes",
    )
    op.drop_table("goal_allocation_bucket_asset_classes")

    op.drop_index(
        "ix_goal_allocation_bucket_subgroups_bucket_id",
        table_name="goal_allocation_bucket_subgroups",
    )
    op.drop_table("goal_allocation_bucket_subgroups")

    op.drop_index(
        "ix_goal_allocation_bucket_goals_goal_id", table_name="goal_allocation_bucket_goals"
    )
    op.drop_index(
        "ix_goal_allocation_bucket_goals_bucket_id", table_name="goal_allocation_bucket_goals"
    )
    op.drop_table("goal_allocation_bucket_goals")

    op.drop_index("ix_goal_allocation_buckets_run_id", table_name="goal_allocation_buckets")
    op.drop_table("goal_allocation_buckets")

    op.drop_index(
        "ix_goal_allocation_goals_financial_goal_id", table_name="goal_allocation_goals"
    )
    op.drop_index("ix_goal_allocation_goals_run_id", table_name="goal_allocation_goals")
    op.drop_table("goal_allocation_goals")

    op.drop_index("ix_goal_allocation_runs_chat_session_id", table_name="goal_allocation_runs")
    op.drop_index("ix_goal_allocation_runs_portfolio_id", table_name="goal_allocation_runs")
    op.drop_index("ix_goal_allocation_runs_user_id", table_name="goal_allocation_runs")
    op.drop_table("goal_allocation_runs")

    bind = op.get_bind()
    WARNING_CODE.drop(bind, checkfirst=True)
    TRADE_EXECUTION_STATUS.drop(bind, checkfirst=True)
    TRADE_ACTION.drop(bind, checkfirst=True)
    TAX_REGIME.drop(bind, checkfirst=True)
    REBALANCING_RUN_STATUS.drop(bind, checkfirst=True)
    ASSET_CLASS_SPLIT_KIND.drop(bind, checkfirst=True)
    ALLOCATION_BUCKET_NAME.drop(bind, checkfirst=True)
    GOAL_ALLOCATION_RUN_STATUS.drop(bind, checkfirst=True)

    # Recreate old tables in the shape they had at HEAD prior to this migration.
    OLD_REBALANCING_STATUS.create(bind, checkfirst=True)
    OLD_RECOMMENDATION_TYPE.create(bind, checkfirst=True)

    op.create_table(
        "rebalancing_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recommendation_type", OLD_RECOMMENDATION_TYPE, nullable=False),
        sa.Column(
            "source_allocation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_recommendations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", OLD_REBALANCING_STATUS, server_default="pending"),
        sa.Column("recommendation_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_rebrec_recommendation_type", "rebalancing_recommendations", ["recommendation_type"]
    )

    op.create_table(
        "goal_allocation_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_investable_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("equity_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("debt_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("others_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("equity_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("debt_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("others_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("suggested_funds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggested_funds_total_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_goal_allocation_recommendations_user_id",
        "goal_allocation_recommendations",
        ["user_id"],
    )
    op.create_index(
        "ix_goal_allocation_recommendations_portfolio_id",
        "goal_allocation_recommendations",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_goal_allocation_recommendations_chat_session_id",
        "goal_allocation_recommendations",
        ["chat_session_id"],
    )

    op.create_table(
        "rebalancing_recommendation_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rebalancing_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("chat_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_question", sa.Text(), nullable=True),
        sa.Column("spine_mode", sa.String(80), nullable=True),
        sa.Column("grand_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("all_amounts_in_multiples_of_100", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "rebalancing_recommendation_id",
            name="uq_rebalancing_recommendation_summary_recommendation",
        ),
    )

    op.create_table(
        "rebalancing_bucket_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rebalancing_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket_name", sa.String(40), nullable=False),
        sa.Column("total_goal_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("allocated_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rebalancing_bucket_goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bucket_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_bucket_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal_name", sa.String(150), nullable=False),
        sa.Column("time_to_goal_months", sa.Integer(), nullable=True),
        sa.Column("amount_needed", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("goal_priority", sa.String(40), nullable=True),
        sa.Column("investment_goal", sa.String(60), nullable=True),
        sa.Column("goal_rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rebalancing_bucket_subgroup_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bucket_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_bucket_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subgroup", sa.String(80), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("pct_of_bucket", sa.Numeric(7, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rebalancing_asset_class_breakdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rebalancing_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("breakdown_kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="bucket"),
        sa.Column("bucket_name", sa.String(40), nullable=True),
        sa.Column("equity_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("debt_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("others_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("equity_pct", sa.Numeric(7, 2), nullable=True),
        sa.Column("debt_pct", sa.Numeric(7, 2), nullable=True),
        sa.Column("others_pct", sa.Numeric(7, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rebalancing_future_investments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rebalancing_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket_name", sa.String(40), nullable=False),
        sa.Column("future_investment_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
