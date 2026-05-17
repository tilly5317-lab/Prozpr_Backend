"""Stage 7: shared corpus pool evolution + per-goal funding allocation.

Critical algorithm: single shared corpus pool with proportional shortfall split at payout months.
Per Excel parity audit, this replaces the per-goal balance evolution model.
"""
from __future__ import annotations
from datetime import date
from cashflow_statement.models import (
    GoalFundingStatus, OneOffFundingStatus, OneOffEvent, MonthlyCashflowRow,
)
from cashflow_statement.engine._types import (
    RunContext, GoalInternal, FundingResult,
)
from cashflow_statement.engine.dates import fy_for_date


def monthly_invest_or_withdraw(
    m: date,
    savings_post_emi: float,
    user_sip: float | None,
    invest_growth: float,
    base_year: int,
    sip_share: float,
    retirement_date: date | None,
) -> tuple[float, str]:
    """M147 4-branch decision rule for monthly invest/withdraw.

    Returns (amount, kind) where kind ∈ {"user_sip", "user_sip_capped", "savings_sip_fraction", "withdrawal", "zero"}.
    "user_sip_capped" means the grown user-SIP exceeded `savings_post_emi` and was clamped to it.

    Post-retirement check is month-level (`m > retirement_date`),
    so the partial retirement FY correctly stops investing from `retirement_date` onward.
    """
    m_year = fy_for_date(m)
    if retirement_date is not None and m > retirement_date:
        return 0.0, "zero"
    if user_sip is not None and user_sip > 100:
        # The stated user SIP is capped at the household's
        # actual post-EMI savings_post_emi for the month. This prevents the engine from
        # "magic-ing up money" when EMIs + expense leave less than the stated SIP.
        # When savings_post_emi is non-positive, the cap means zero.
        if savings_post_emi <= 0:
            return 0.0, "zero"
        grown = user_sip * (1 + invest_growth) ** (m_year - base_year)
        if grown > savings_post_emi:
            return savings_post_emi, "user_sip_capped"
        return grown, "user_sip"
    if savings_post_emi > 0:
        return savings_post_emi * sip_share, "savings_sip_fraction"
    return savings_post_emi, "withdrawal"


def compute_funding(
    goals_internal: list[GoalInternal],
    ctx: RunContext,
    monthly_cashflow: list[MonthlyCashflowRow],
    one_off_inflows: list[OneOffEvent],
    one_off_outflows: list[OneOffEvent],
    warnings: list[str],
) -> FundingResult:
    """Single shared corpus pool, proportional shortfall split at outflow months.

    Emits a per-month list of `MonthlyCashflowRow`s with the corpus-side fields
    filled in (rows are copies of the input `monthly_cashflow` rows with the
    corpus_opening / monthly_investment / roi / goal_payout / corpus_closing / is_funded
    fields populated). Goal payouts and one-off outflows are kept on separate
    fields (`goal_payout` vs `one_off_outflow`) to avoid double-counting.
    """
    corpus = ctx.corpus
    monthly_enriched: list[MonthlyCashflowRow] = []
    # Per-event shortfall accumulators — TWO separate dicts so a goal name
    # cannot collide with a one-off description and corrupt attribution. The
    # combined exposed dicts at the end are prefix-namespaced for the same reason.
    goal_underfunded: dict[str, float] = {g.name: 0.0 for g in goals_internal}
    one_off_underfunded: dict[str, float] = {e.description: 0.0 for e in one_off_outflows}

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
        corpus_opening = corpus

        # M147 invest/withdraw
        monthly_investment, kind = monthly_invest_or_withdraw(
            m=m,
            savings_post_emi=cf.savings_post_emi,
            user_sip=ctx.starting_monthly_investment,
            invest_growth=ctx.annual_invested_amount_growth,
            base_year=base_year,
            sip_share=ctx.sip_share,
            retirement_date=ctx.retirement_date_considered,
        )

        # 3-band ROI for shared pool — mid_term_roi during years 2-5.
        # Matches expected_roi_for_goal in goals_table.py, so investment_required_pv ↔ corpus growth align.
        # Clamp at 0 when corpus negative — portfolio ROI can't "earn" on debt.
        if m <= ctx.near_term_end:
            roi_annual = ctx.near_term_roi
        elif m <= ctx.medium_term_end:
            roi_annual = ctx.mid_term_roi
        else:
            roi_annual = ctx.long_term_roi
        roi = max(corpus_opening * ((1 + roi_annual) ** (1/12) - 1), 0)

        # One-off inflow this month (sourced from cf row — populated by project_cashflow).
        oin = cf.one_off_inflow

        # Outflows this month — kept on separate dicts so attribution is
        # correct even if a goal and a one-off share a name.
        goal_outflows_this: dict[str, float] = {}
        goal_payout = 0.0
        for g in goal_by_ym.get(ym, []):
            goal_outflows_this[g.name] = g.corpus_required_fv
            goal_payout += g.corpus_required_fv
        one_off_outflows_this: dict[str, float] = {}
        for e in out_by_ym.get(ym, []):
            one_off_outflows_this[e.description] = (
                one_off_outflows_this.get(e.description, 0.0) + e.amount
            )
        one_off_outflow = cf.one_off_outflow
        total_outflow = goal_payout + one_off_outflow

        corpus = corpus_opening + monthly_investment + roi + oin - total_outflow

        funded = True
        if total_outflow > 0:
            available = corpus_opening + monthly_investment + roi + oin
            if available >= total_outflow:
                funded = True
            else:
                funded = False
                shortfall = total_outflow - max(available, 0.0)
                for name, amt in goal_outflows_this.items():
                    weight = amt / total_outflow
                    goal_underfunded[name] = goal_underfunded.get(name, 0.0) + shortfall * weight
                for desc, amt in one_off_outflows_this.items():
                    weight = amt / total_outflow
                    one_off_underfunded[desc] = one_off_underfunded.get(desc, 0.0) + shortfall * weight

        monthly_enriched.append(cf.model_copy(update={
            "corpus_opening": corpus_opening,
            "monthly_investment": monthly_investment,
            "investment_source": kind,
            "investment_returns": roi,
            "goal_payout": goal_payout,
            "corpus_closing": corpus,
            "is_funded": funded,
        }))

    corpus_closing = corpus

    # Build per-goal status (lookups read from goal_underfunded only).
    per_goal_status: list[GoalFundingStatus] = []
    for g in goals_internal:
        underfunded = goal_underfunded.get(g.name, 0.0)
        funded_amt = g.corpus_required_fv - underfunded
        per_goal_status.append(GoalFundingStatus(
            name=g.name, goal_type=g.goal_type, goal_date=g.goal_date,
            goal_value_pv=g.goal_value_pv, goal_value_fv=g.goal_value_fv,
            corpus_required_fv=g.corpus_required_fv,
            investment_required_pv=g.investment_required_pv, funded_amount=funded_amt,
            is_funded=(underfunded == 0), shortfall_fv=underfunded,
            expected_roi=g.expected_roi,
        ))

    per_one_off_outflow_status: list[OneOffFundingStatus] = []
    for e in one_off_outflows:
        underfunded = one_off_underfunded.get(e.description, 0.0)
        funded_amt = e.amount - underfunded
        per_one_off_outflow_status.append(OneOffFundingStatus(
            description=e.description, date=e.date, amount=e.amount,
            funded_amount=funded_amt, is_funded=(underfunded == 0), shortfall=underfunded,
        ))

    return FundingResult(
        monthly_enriched=monthly_enriched,
        corpus_closing=corpus_closing,
        per_goal_status=per_goal_status,
        per_one_off_outflow_status=per_one_off_outflow_status,
    )
