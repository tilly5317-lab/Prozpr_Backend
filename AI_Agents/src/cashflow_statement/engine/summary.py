"""Stage 8: Summary — HeadlineStatus + FundFlowSummary."""
from __future__ import annotations
from cashflow_statement.models import (
    HeadlineStatus, FundFlowSummary, OneOffEvent,
)
from cashflow_statement.engine._types import RunContext, GoalInternal, FundingResult
from cashflow_statement.engine.dates import fy_end_after


def build_headline_status(
    ctx: RunContext,
    goals_internal: list[GoalInternal],
    funding: FundingResult,
    one_off_outflows: list[OneOffEvent],
) -> HeadlineStatus:
    sum_fund_pv = sum(g.investment_required_pv for g in goals_internal)
    surplus_or_shortfall_today = ctx.corpus - sum_fund_pv

    # Include one-off outflow dates in last_goal_date / last_fy_end_date.
    candidate_dates = [g.goal_date for g in goals_internal]
    candidate_dates += [e.date for e in one_off_outflows if e.date > ctx.latest_update_date]
    last_goal_date = max(candidate_dates, default=ctx.latest_update_date)
    candidate_fys = [g.goal_date_fy for g in goals_internal]
    # Wrap one-off outflow dates with fy_end_after() so candidate_fys stays homogeneous
    # — FY-end (March 31) dates only. Mixing raw dates would make max() return a non-FY
    # boundary when the latest event is a one-off (bug fix).
    candidate_fys += [fy_end_after(e.date) for e in one_off_outflows if e.date > ctx.latest_update_date]
    last_fy_end_date = max(candidate_fys, default=ctx.current_fy_end)

    total_shortfall = sum(s.shortfall_fv for s in funding.per_goal_status)
    total_funded = sum(s.funded_amount for s in funding.per_goal_status)
    years_to_last_goal = max(last_fy_end_date.year - ctx.current_fy_year, 0)

    is_feasible = (
        all(s.is_funded for s in funding.per_goal_status)
        and funding.corpus_closing >= 0
    )

    return HeadlineStatus(
        years_to_last_goal=years_to_last_goal,
        last_goal_date=last_goal_date,
        last_fy_end_date=last_fy_end_date,
        number_of_goals=len(goals_internal),
        corpus_today=ctx.corpus,
        total_corpus_required_today=sum_fund_pv,
        surplus_or_shortfall_today=surplus_or_shortfall_today,
        corpus_closing=funding.corpus_closing,
        is_feasible=is_feasible,
        total_shortfall_fv=total_shortfall,
        total_funded_amount=total_funded,
    )


def build_fund_flow_summary(
    ctx: RunContext,
    goals_internal: list[GoalInternal],
    funding: FundingResult,
) -> FundFlowSummary:
    # Signed sum: positive=SIP, negative=withdrawal. One-off and goal totals are
    # stored as positive magnitudes — the bridge formula subtracts the latter two.
    total_invest = sum(r.monthly_investment for r in funding.monthly_enriched)
    total_roi = sum(r.investment_returns for r in funding.monthly_enriched)
    total_one_off_in = sum(r.one_off_inflow for r in funding.monthly_enriched)
    total_one_off_out = sum(r.one_off_outflow for r in funding.monthly_enriched)
    total_goals_paid = sum(r.goal_payout for r in funding.monthly_enriched)
    sum_fund_pv = sum(g.investment_required_pv for g in goals_internal)
    return FundFlowSummary(
        corpus_opening=ctx.corpus,
        total_investments=total_invest,
        total_roi=total_roi,
        total_one_off_in=total_one_off_in,
        total_one_off_out=total_one_off_out,
        total_goals_paid=total_goals_paid,
        corpus_closing=funding.corpus_closing,
        corpus_today=ctx.corpus,
        total_corpus_required_today=sum_fund_pv,
        surplus_or_shortfall_today=ctx.corpus - sum_fund_pv,
    )
