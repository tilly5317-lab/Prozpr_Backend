"""Stage 7: shared NFA pool evolution + per-goal funding allocation.

Critical algorithm: single shared NFA pool with proportional shortfall split at payout months.
Per Excel parity audit, this replaces the per-goal balance evolution model.
"""
from __future__ import annotations
from datetime import date
from goal_planning.models import (
    GoalFundingStatus, OneOffFundingStatus, OneOffEvent, MonthlyNFARow, MonthlyCashflowRow,
)
from goal_planning.engine._types import (
    RunContext, GoalInternal, FundingResult,
)
from goal_planning.engine.dates import fy_for_date


def monthly_invest_or_withdraw(
    m: date,
    savings_2_avg: float,
    user_sip: float | None,
    invest_growth: float,
    base_year: int,
    sip_share: float,
    retirement_date: date | None,
) -> tuple[float, str]:
    """M147 4-branch decision rule for monthly invest/withdraw.

    Returns (amount, kind) where kind ∈ {"user_sip", "savings_sip_fraction", "withdrawal", "zero"}.

    Per spec §8.4 G3:
    - if m_year > retirement_year → 0, "zero"
    - elif m_year < retirement_year AND user_sip is not None AND user_sip > 100 →
          user_sip × (1+invest_growth)^(m_year - base_year), "user_sip"
    - else (m_year == retirement_year OR user_sip absent OR user_sip <= 100) → K-based:
        - if savings_2_avg > 0 → savings_2_avg × sip_share, "savings_sip_fraction"
        - else → savings_2_avg (negative), "withdrawal"
    """
    m_year = fy_for_date(m)
    if retirement_date is not None:
        ret_year = fy_for_date(retirement_date)
    else:
        ret_year = m_year + 1000  # never reached → treat as pre-retirement always

    if m_year > ret_year:
        return 0.0, "zero"
    if m_year < ret_year and user_sip is not None and user_sip > 100:
        return user_sip * (1 + invest_growth) ** (m_year - base_year), "user_sip"
    if savings_2_avg > 0:
        return savings_2_avg * sip_share, "savings_sip_fraction"
    return savings_2_avg, "withdrawal"


def compute_funding(
    goals_internal: list[GoalInternal],
    ctx: RunContext,
    monthly_cashflow: list[MonthlyCashflowRow],
    one_off_inflows: list[OneOffEvent],
    one_off_outflows: list[OneOffEvent],
    warnings: list[str],
) -> FundingResult:
    """Single shared NFA pool, proportional shortfall split at outflow months."""
    nfa = ctx.nfa
    nfa_monthly: list[MonthlyNFARow] = []
    per_outflow_underfunded: dict[str, float] = {}
    # Pre-init keys
    for g in goals_internal:
        per_outflow_underfunded[g.name] = 0.0
    for e in one_off_outflows:
        per_outflow_underfunded[e.description] = 0.0

    # Group payouts by month_end (last day of month)
    # For each cashflow row m, gather any goal whose goal_date_fy is the same FY AND goal_date.month == m.month
    # Simpler: index goals by (goal_date.year, goal_date.month) and one-off-outflows similarly
    goal_by_ym: dict[tuple[int, int], list[GoalInternal]] = {}
    for g in goals_internal:
        goal_by_ym.setdefault((g.goal_date.year, g.goal_date.month), []).append(g)
    out_by_ym: dict[tuple[int, int], list[OneOffEvent]] = {}
    for e in one_off_outflows:
        out_by_ym.setdefault((e.date.year, e.date.month), []).append(e)
    in_by_ym: dict[tuple[int, int], list[OneOffEvent]] = {}
    for e in one_off_inflows:
        in_by_ym.setdefault((e.date.year, e.date.month), []).append(e)

    base_year = ctx.current_fy_year

    for cf in monthly_cashflow:
        m = cf.month_end_date
        ym = (m.year, m.month)
        nfa_open = nfa

        # M147 invest/withdraw
        regular_invest, kind = monthly_invest_or_withdraw(
            m=m,
            savings_2_avg=cf.savings_2_avg,
            user_sip=ctx.monthly_investment_next_12m,
            invest_growth=ctx.annual_invested_amount_growth,
            base_year=base_year,
            sip_share=ctx.sip_share,
            retirement_date=ctx.retirement_date_considered,
        )

        # 2-band ROI for shared pool (near vs long; mid unused per spec)
        roi_annual = ctx.near_term_roi if m <= ctx.near_term_end else ctx.long_term_roi
        roi = nfa_open * ((1 + roi_annual) ** (1/12) - 1)

        # One-off inflow this month
        oin = sum(e.amount for e in in_by_ym.get(ym, []))

        # Outflows this month: goals + one-off-outflows
        outflows: dict[str, float] = {}
        for g in goal_by_ym.get(ym, []):
            outflows[g.name] = g.amount_fv
        for e in out_by_ym.get(ym, []):
            # Could collide with goal name; spec validator prevents but defensive:
            outflows[e.description] = outflows.get(e.description, 0.0) + e.amount
        outflow_total = sum(outflows.values())

        nfa = nfa_open + regular_invest + roi + oin - outflow_total

        funded = True
        if outflow_total > 0:
            available = nfa_open + regular_invest + roi + oin
            if available >= outflow_total:
                funded = True
            else:
                funded = False
                shortfall = outflow_total - max(available, 0.0)
                for name, amt in outflows.items():
                    weight = amt / outflow_total
                    per_outflow_underfunded[name] = per_outflow_underfunded.get(name, 0.0) + shortfall * weight

        nfa_monthly.append(MonthlyNFARow(
            month_end=m, fy_label=cf.fy_label, nfa_open=nfa_open,
            regular_invest=regular_invest, regular_invest_kind=kind,
            roi=roi, one_off_in=oin, goal_outflow_total=outflow_total,
            nfa_close=nfa, savings_2_avg=cf.savings_2_avg, funded_flag=funded,
        ))

    closing_nfa = nfa
    min_nfa = min((r.nfa_close for r in nfa_monthly), default=ctx.nfa)

    # Build per-goal status
    per_goal_status: list[GoalFundingStatus] = []
    per_outflow_funded_amount: dict[str, float] = {}
    for g in goals_internal:
        underfunded = per_outflow_underfunded.get(g.name, 0.0)
        funded_amt = g.amount_fv - underfunded
        per_outflow_funded_amount[g.name] = funded_amt
        years_to = max((g.goal_date - ctx.latest_update_date).days / 365.25, 1e-9)
        shortfall_pv = underfunded / (1 + g.expected_roi) ** years_to
        per_goal_status.append(GoalFundingStatus(
            name=g.name, goal_type=g.goal_type, goal_date=g.goal_date,
            amount_pv=g.amount_pv, amount_fv=g.amount_fv,
            fund_today_pv=g.fund_today_pv, funded_amount=funded_amt,
            is_funded=(underfunded == 0), shortfall_fv=underfunded,
            shortfall_pv=shortfall_pv, expected_roi=g.expected_roi,
        ))

    per_one_off_outflow_status: list[OneOffFundingStatus] = []
    for e in one_off_outflows:
        underfunded = per_outflow_underfunded.get(e.description, 0.0)
        funded_amt = e.amount - underfunded
        per_outflow_funded_amount[e.description] = funded_amt
        per_one_off_outflow_status.append(OneOffFundingStatus(
            description=e.description, date=e.date, amount=e.amount,
            funded_amount=funded_amt, is_funded=(underfunded == 0), shortfall=underfunded,
        ))

    return FundingResult(
        nfa_monthly=nfa_monthly,
        closing_nfa=closing_nfa,
        min_nfa_in_horizon=min_nfa,
        per_goal_status=per_goal_status,
        per_one_off_outflow_status=per_one_off_outflow_status,
        per_outflow_underfunded_total=per_outflow_underfunded,
        per_outflow_funded_amount=per_outflow_funded_amount,
    )
