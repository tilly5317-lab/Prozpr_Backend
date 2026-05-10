"""Persist a rebalancing engine response across the normalized
``rebalancing_*`` tables.

A run always references a ``GoalAllocationRun`` (the target it rebalanced
towards) — ``source_allocation_run_id`` is required.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rebalancing.rebalancing_fund_row import RebalancingFundRow
from app.models.rebalancing.rebalancing_run import (
    RebalancingRun,
    RebalancingRunStatus,
    RebalancingTotals,
    TaxRegime,
)
from app.models.rebalancing.rebalancing_subgroup_summary import (
    RebalancingSubgroupSummary,
)
from app.models.rebalancing.rebalancing_trade import (
    RebalancingTrade,
    TradeAction,
    TradeExecutionStatus,
)
from app.models.rebalancing.rebalancing_warning import (
    RebalancingWarning,
    RebalancingWarningCode,
)
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.portfolio_service import get_or_create_primary_portfolio

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
    RebalancingComputeRequest,
    RebalancingComputeResponse,
)


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def persist_rebalancing_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    response: RebalancingComputeResponse,
    *,
    source_allocation_run_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID] = None,
    used_cached_allocation: bool = False,
    user_question: Optional[str] = None,
    request: Optional[RebalancingComputeRequest] = None,
) -> uuid.UUID:
    """Write the engine response and return the new ``RebalancingRun`` id."""
    portfolio = await get_or_create_primary_portfolio(db, user_id)

    metadata = response.metadata
    knob = metadata.knob_snapshot

    # Default tax/corpus from request when available; otherwise fall back to
    # metadata. ``request`` is optional for backward compatibility but is
    # strongly recommended so per-request fields are captured.
    tax_regime_value = request.tax_regime if request else "new"
    effective_tax_rate = request.effective_tax_rate_pct if request else 0.0
    rounding_step = request.rounding_step if request else 100
    stcg_offset_budget = request.stcg_offset_budget_inr if request else None
    cf_st = request.carryforward_st_loss_inr if request else Decimal(0)
    cf_lt = request.carryforward_lt_loss_inr if request else Decimal(0)

    run = RebalancingRun(
        user_id=user_id,
        portfolio_id=portfolio.id,
        chat_session_id=chat_session_id,
        source_allocation_run_id=source_allocation_run_id,
        status=RebalancingRunStatus.pending,
        engine_request_id=metadata.request_id,
        engine_version=metadata.engine_version,
        computed_at=metadata.computed_at,
        tax_regime=TaxRegime(tax_regime_value),
        effective_tax_rate_pct=float(effective_tax_rate),
        total_corpus=_to_decimal(metadata.request_corpus_inr),
        rounding_step=rounding_step,
        stcg_offset_budget_inr=_to_decimal(stcg_offset_budget) if stcg_offset_budget is not None else None,
        carryforward_st_loss_inr=_to_decimal(cf_st),
        carryforward_lt_loss_inr=_to_decimal(cf_lt),
        knob_snapshot=knob.model_dump(mode="json"),
        request_input=request.model_dump(mode="json") if request else None,
        used_cached_allocation=used_cached_allocation,
        user_question=user_question,
    )
    db.add(run)
    await db.flush()

    # Totals.
    totals = response.totals
    db.add(
        RebalancingTotals(
            run_id=run.id,
            total_buy_inr=_to_decimal(totals.total_buy_inr),
            total_sell_inr=_to_decimal(totals.total_sell_inr),
            net_cash_flow_inr=_to_decimal(totals.net_cash_flow_inr),
            total_stcg_realised=_to_decimal(totals.total_stcg_realised),
            total_ltcg_realised=_to_decimal(totals.total_ltcg_realised),
            total_stcg_net_off=_to_decimal(totals.total_stcg_net_off),
            total_tax_estimate_inr=_to_decimal(totals.total_tax_estimate_inr),
            total_exit_load_inr=_to_decimal(totals.total_exit_load_inr),
            unrebalanced_remainder_inr=_to_decimal(totals.unrebalanced_remainder_inr),
            rows_count=totals.rows_count,
            funds_to_buy_count=totals.funds_to_buy_count,
            funds_to_sell_count=totals.funds_to_sell_count,
            funds_to_exit_count=totals.funds_to_exit_count,
            funds_held_count=totals.funds_held_count,
        )
    )

    # Subgroup summaries.
    for sg in response.subgroups:
        db.add(
            RebalancingSubgroupSummary(
                run_id=run.id,
                asset_subgroup=sg.asset_subgroup,
                goal_target_inr=_to_decimal(sg.goal_target_inr),
                current_holding_inr=_to_decimal(sg.current_holding_inr),
                suggested_final_holding_inr=_to_decimal(sg.suggested_final_holding_inr),
                rebalance_inr=_to_decimal(sg.rebalance_inr),
                total_buy_inr=_to_decimal(sg.total_buy_inr),
                total_sell_inr=_to_decimal(sg.total_sell_inr),
                ranks_total=sg.ranks_total,
                ranks_with_holding=sg.ranks_with_holding,
                ranks_with_action=sg.ranks_with_action,
            )
        )

    # Per-fund audit rows.
    row: FundRowAfterStep5
    for row in response.rows:
        db.add(
            RebalancingFundRow(
                run_id=run.id,
                isin=row.isin,
                recommended_fund=row.recommended_fund,
                asset_subgroup=row.asset_subgroup,
                sub_category=row.sub_category,
                rank=row.rank,
                fund_rating=row.fund_rating,
                is_recommended=row.is_recommended,
                target_amount_pre_cap=_to_decimal(row.target_amount_pre_cap),
                max_pct=row.max_pct,
                target_pre_cap_pct=row.target_pre_cap_pct,
                target_own_capped_pct=row.target_own_capped_pct,
                final_target_pct=row.final_target_pct,
                final_target_amount=_to_decimal(row.final_target_amount),
                present_allocation_inr=_to_decimal(row.present_allocation_inr),
                invested_cost_inr=_to_decimal(row.invested_cost_inr),
                st_value_inr=_to_decimal(row.st_value_inr),
                st_cost_inr=_to_decimal(row.st_cost_inr),
                lt_value_inr=_to_decimal(row.lt_value_inr),
                lt_cost_inr=_to_decimal(row.lt_cost_inr),
                exit_load_pct=row.exit_load_pct,
                exit_load_months=row.exit_load_months,
                units_within_exit_load_period=_to_decimal(row.units_within_exit_load_period),
                current_nav=_to_decimal(row.current_nav),
                exit_load_amount=_to_decimal(row.exit_load_amount),
                diff=_to_decimal(row.diff),
                exit_flag=row.exit_flag,
                worth_to_change=row.worth_to_change,
                stcg_amount=_to_decimal(row.stcg_amount),
                ltcg_amount=_to_decimal(row.ltcg_amount),
                pass1_buy_amount=_to_decimal(row.pass1_buy_amount),
                pass1_underbuy_amount=_to_decimal(row.pass1_underbuy_amount),
                pass1_sell_amount=_to_decimal(row.pass1_sell_amount),
                pass1_undersell_amount=_to_decimal(row.pass1_undersell_amount),
                pass1_sell_lt_amount=_to_decimal(row.pass1_sell_lt_amount),
                pass1_realised_ltcg=_to_decimal(row.pass1_realised_ltcg),
                pass1_sell_st_amount=_to_decimal(row.pass1_sell_st_amount),
                pass1_realised_stcg=_to_decimal(row.pass1_realised_stcg),
                stcg_budget_remaining_after_pass1=_to_decimal(row.stcg_budget_remaining_after_pass1),
                pass1_sell_amount_no_stcg_cap=_to_decimal(row.pass1_sell_amount_no_stcg_cap),
                pass1_undersell_due_to_stcg_cap=_to_decimal(row.pass1_undersell_due_to_stcg_cap),
                pass1_blocked_stcg_value=_to_decimal(row.pass1_blocked_stcg_value),
                holding_after_initial_trades=_to_decimal(row.holding_after_initial_trades),
                stcg_offset_amount=_to_decimal(row.stcg_offset_amount),
                pass2_sell_amount=_to_decimal(row.pass2_sell_amount),
                pass2_undersell_amount=_to_decimal(row.pass2_undersell_amount),
                final_holding_amount=_to_decimal(row.final_holding_amount),
            )
        )

    # Trades.
    for trade in response.trade_list:
        db.add(
            RebalancingTrade(
                run_id=run.id,
                isin=trade.isin,
                recommended_fund=trade.recommended_fund,
                asset_subgroup=trade.asset_subgroup,
                sub_category=trade.sub_category,
                action=TradeAction(trade.action),
                amount_inr=_to_decimal(trade.amount_inr),
                reason_code=trade.reason_code,
                reason_title=trade.reason_title,
                reason_text=trade.reason_text,
                execution_status=TradeExecutionStatus.pending,
            )
        )

    # Warnings.
    for warning in response.warnings:
        db.add(
            RebalancingWarning(
                run_id=run.id,
                code=RebalancingWarningCode(warning.code.value),
                message=warning.message,
                affected_isins=list(warning.affected_isins),
            )
        )

    await db.flush()
    return run.id
