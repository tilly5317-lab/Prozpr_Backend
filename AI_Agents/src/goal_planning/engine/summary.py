"""Stage 8: Summary — HeadlineStatus + FundFlowSummary."""
from __future__ import annotations
from goal_planning.models import (
    HeadlineStatus, FundFlowSummary, RetirementSnapshot,
    OneOffEvent, AnnualCashflowRow,
)
from goal_planning.engine._types import RunContext, GoalInternal, FundingResult


def build_headline_status(
    ctx: RunContext,
    goals_internal: list[GoalInternal],
    funding: FundingResult,
    retirement: RetirementSnapshot,
    annual_cashflow: list[AnnualCashflowRow],
    warnings: list[str],
) -> HeadlineStatus:
    sum_fund_pv = sum(g.fund_today_pv for g in goals_internal)
    present_status = ctx.nfa - sum_fund_pv
    last_goal_date = max((g.goal_date for g in goals_internal), default=ctx.latest_update_date)
    last_fy_end_date = max((g.goal_date_fy for g in goals_internal), default=ctx.current_fy_end)
    total_shortfall = sum(s.shortfall_fv for s in funding.per_goal_status)
    total_funded = sum(s.funded_amount for s in funding.per_goal_status)

    is_feasible = (
        all(s.is_funded for s in funding.per_goal_status)
        and present_status >= 0
        and funding.min_nfa_in_horizon >= 0
    )

    overall_shortfall_fv = max(total_shortfall, 0)
    overall_shortfall_pv = sum(s.shortfall_pv for s in funding.per_goal_status)
    horizon_years = max(last_fy_end_date.year - ctx.current_fy_year, 0)

    return HeadlineStatus(
        horizon_years=horizon_years,
        last_goal_date=last_goal_date,
        last_fy_end_date=last_fy_end_date,
        number_of_goals=len(goals_internal),
        net_financial_assets_today=ctx.nfa,
        sum_fund_today_pv=sum_fund_pv,
        present_status=present_status,
        closing_nfa=funding.closing_nfa,
        total_shortfall_fv=total_shortfall,
        total_funded_amount=total_funded,
        is_overall_feasible=is_feasible,
        overall_shortfall_pv=overall_shortfall_pv,
        overall_shortfall_fv=overall_shortfall_fv,
    )


def build_fund_flow_summary(
    ctx: RunContext,
    annual_cashflow: list[AnnualCashflowRow],
    funding: FundingResult,
    one_off_inflows: list[OneOffEvent],
    one_off_outflows: list[OneOffEvent],
) -> FundFlowSummary:
    total_invest = sum(r.regular_invest for r in funding.nfa_monthly if r.regular_invest > 0)
    total_roi = sum(r.roi for r in funding.nfa_monthly)
    total_in = sum(e.amount for e in one_off_inflows)
    total_out = sum(e.amount for e in one_off_outflows)
    # goals_paid = total outflows from nfa_monthly minus the one-off-out portion
    total_goal_outflow = sum(r.goal_outflow_total for r in funding.nfa_monthly)
    total_goals_paid = total_goal_outflow - total_out
    return FundFlowSummary(
        opening_nfa=ctx.nfa,
        total_investments=total_invest,
        total_roi=total_roi,
        total_one_off_in=total_in,
        total_one_off_out=total_out,
        total_goals_paid=total_goals_paid,
        closing_nfa=funding.closing_nfa,
    )
