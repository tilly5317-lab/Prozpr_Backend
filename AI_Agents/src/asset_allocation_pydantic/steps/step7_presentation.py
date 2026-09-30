from __future__ import annotations

from typing import List

from ..models import (
    AggregatedSubgroupRow,
    AllocationInput,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAllocation,
    BucketAssetClassSplit,
    ClientSummary,
    FutureInvestment,
    Goal,
    GoalAllocationOutput,
    Step1Output,
    Step2Output,
    Step4Output,
    Step5Output,
    SubgroupBreakdown,
    SubgroupBucketAllocation,
    SubgroupBucketSplit,
)


_BUCKET_RATIONALES: dict[str, str] = {
    "emergency": (
        "We set aside a safety cushion so an unexpected expense won't force you "
        "to touch the rest of your money."
    ),
    "short_term": (
        "For goals coming up soon, the money stays in steady, predictable "
        "options so it's ready when you need it."
    ),
    "long_term": (
        "With many years to go, more of the money can aim for growth since "
        "short-term ups and downs have time to even out."
    ),
}


_INVESTMENT_GOAL_CONTEXT: dict[str, str] = {
    "retirement": "your retirement nest egg",
    "education": "your child's education",
    "home_purchase": "your home purchase",
    "intergenerational_transfer": "what you'll pass on",
    "wealth_creation": "building long-term wealth",
    "other": "this goal",
}


def _horizon_phrase(months: int) -> str:
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} away"
    years = months / 12
    if years == int(years):
        y = int(years)
        return f"{y} year{'s' if y != 1 else ''} away"
    return f"about {years:.1f} years away"


def _goal_rationale(bucket: str, goal: Goal) -> str:
    context = _INVESTMENT_GOAL_CONTEXT.get(goal.investment_goal, "this goal")
    horizon = _horizon_phrase(goal.time_to_goal_months)
    amount_str = f"₹{goal.amount_needed:,.0f}"

    if bucket == "short_term":
        return (
            f"For {goal.goal_name} ({context}) — {horizon}, earmarking "
            f"{amount_str} — the money stays in steady, predictable instruments. "
            f"This close to the deadline, protecting the capital matters more "
            f"than chasing returns."
        )
    return (
        f"For {goal.goal_name} ({context}) — {horizon}, earmarking "
        f"{amount_str} — we lean into growth. Over this horizon compounding "
        f"does the heavy lifting, and short-term dips have plenty of time "
        f"to recover."
    )


def _attach_rationales(bucket_allocations: List[BucketAllocation]) -> None:
    for b in bucket_allocations:
        b.rationale = _BUCKET_RATIONALES.get(b.bucket)
        if b.bucket != "emergency":
            b.goal_rationales = {g.goal_name: _goal_rationale(b.bucket, g) for g in b.goals}


def _client_summary(inp: AllocationInput, step1: Step1Output) -> ClientSummary:
    return ClientSummary(
        age=inp.age,
        occupation=inp.occupation_type,
        effective_risk_score=inp.effective_risk_score,
        total_corpus=inp.total_corpus,
        goals=list(inp.goals),
        emergency_fund_months=step1.emergency_fund_months,
        monthly_household_expense=inp.monthly_household_expense,
    )


def _bucket_allocations(
    inp: AllocationInput,
    step1: Step1Output,
    step2: Step2Output,
    step4: Step4Output,
) -> List[BucketAllocation]:
    emergency = BucketAllocation(
        bucket="emergency",
        goals=[],
        total_goal_amount=step1.total_emergency,
        allocated_amount=step1.total_emergency,
        future_investment=step1.future_investment,
        subgroup_amounts=dict(step1.subgroup_amounts),
    )

    short_term = BucketAllocation(
        bucket="short_term",
        goals=list(step2.goals_allocated),
        total_goal_amount=step2.total_goal_amount,
        allocated_amount=step2.allocated_amount,
        future_investment=step2.future_investment,
        subgroup_amounts=dict(step2.subgroup_amounts),
    )

    long_term = BucketAllocation(
        bucket="long_term",
        goals=list(step4.goals_allocated),
        total_goal_amount=sum(g.amount_needed for g in step4.goals_allocated),
        allocated_amount=step4.total_allocated,
        future_investment=step4.future_investment,
        subgroup_amounts=dict(step4.subgroup_amounts),
    )

    return [emergency, short_term, long_term]


def _pct(numer: float, denom: float) -> float:
    return round(100.0 * numer / denom, 2) if denom > 0 else 0.0


def _with_pcts(split: BucketAssetClassSplit) -> BucketAssetClassSplit:
    total = split.equity + split.debt + split.others
    return BucketAssetClassSplit(
        bucket=split.bucket,
        equity=split.equity,
        debt=split.debt,
        others=split.others,
        equity_pct=_pct(split.equity, total),
        debt_pct=_pct(split.debt, total),
        others_pct=_pct(split.others, total),
    )


def _split_block(per_bucket: List[BucketAssetClassSplit]) -> AssetClassSplitBlock:
    with_pcts = [_with_pcts(b) for b in per_bucket]
    eq = sum(b.equity for b in with_pcts)
    dt = sum(b.debt for b in with_pcts)
    oth = sum(b.others for b in with_pcts)
    grand = eq + dt + oth
    return AssetClassSplitBlock(
        per_bucket=with_pcts,
        equity_total=eq,
        debt_total=dt,
        others_total=oth,
        equity_total_pct=_pct(eq, grand),
        debt_total_pct=_pct(dt, grand),
        others_total_pct=_pct(oth, grand),
    )


def _subgroup_bucket(bucket: str, amounts: dict[str, int]) -> SubgroupBucketSplit:
    total = sum(amounts.values())
    rows = [
        SubgroupBucketAllocation(
            subgroup=sg, amount=amt, pct_of_bucket=_pct(amt, total)
        )
        for sg, amt in amounts.items()
        if amt > 0
    ]
    return SubgroupBucketSplit(bucket=bucket, subgroups=rows)


def _subgroup_breakdown(
    step1: Step1Output,
    step2: Step2Output,
    step4: Step4Output,
) -> SubgroupBreakdown:
    emergency_subs = dict(step1.subgroup_amounts)
    short_subs = dict(step2.subgroup_amounts)
    long_recommended = dict(step4.subgroup_amounts)
    long_planned = dict(step4.planned_subgroup_amounts or step4.subgroup_amounts)

    # Emergency/short: planned == recommended at subgroup level.
    planned = [
        _subgroup_bucket("emergency", emergency_subs),
        _subgroup_bucket("short_term", short_subs),
        _subgroup_bucket("long_term", long_planned),
    ]
    recommended = [
        _subgroup_bucket("emergency", emergency_subs),
        _subgroup_bucket("short_term", short_subs),
        _subgroup_bucket("long_term", long_recommended),
    ]
    return SubgroupBreakdown(planned=planned, recommended=recommended)


def _asset_class_breakdown(
    inp: AllocationInput,
    step1: Step1Output,
    step2: Step2Output,
    step4: Step4Output,
    grand_total: int,
) -> AssetClassBreakdown:
    # Emergency and short-term are all-debt in both planned and actual views.
    emergency = BucketAssetClassSplit(
        bucket="emergency",
        equity=0,
        debt=step1.total_emergency,
        others=0,
    )
    short_term = BucketAssetClassSplit(
        bucket="short_term",
        equity=0,
        debt=step2.allocated_amount,
        others=0,
    )

    # Long-term planned: Phase-2 output (before multi-asset/overage).
    ac_planned = step4.planned_asset_class_allocation or step4.asset_class_allocation
    long_planned = BucketAssetClassSplit(
        bucket="long_term",
        equity=ac_planned.equities_amount,
        debt=ac_planned.debt_amount,
        others=ac_planned.others_amount,
    )

    # Long-term recommended: Step-4 post-overage split.
    ac_recommended = step4.asset_class_allocation
    long_recommended = BucketAssetClassSplit(
        bucket="long_term",
        equity=ac_recommended.equities_amount,
        debt=ac_recommended.debt_amount,
        others=ac_recommended.others_amount,
    )

    planned_block = _split_block([emergency, short_term, long_planned])
    recommended_block = _split_block([emergency, short_term, long_recommended])
    recommended_sum = (
        recommended_block.equity_total
        + recommended_block.debt_total
        + recommended_block.others_total
    )

    return AssetClassBreakdown(
        planned=planned_block,
        recommended=recommended_block,
        recommended_sum_matches_grand_total=(recommended_sum == grand_total),
        subgroups=_subgroup_breakdown(step1, step2, step4),
    )


def _aggregated_subgroups(step5: Step5Output) -> List[AggregatedSubgroupRow]:
    return [
        AggregatedSubgroupRow(
            subgroup=r.subgroup,
            emergency=r.emergency,
            short_term=r.short_term,
            medium_term=r.medium_term,
            long_term=r.long_term,
            total=r.total,
        )
        for r in step5.rows
    ]


def run(
    inp: AllocationInput,
    step1: Step1Output,
    step2: Step2Output,
    step4: Step4Output,
    step5: Step5Output,
) -> GoalAllocationOutput:
    client_summary = _client_summary(inp, step1)
    bucket_allocations = _bucket_allocations(inp, step1, step2, step4)
    aggregated_subgroups = _aggregated_subgroups(step5)

    future_investments_summary: List[FutureInvestment] = [
        b.future_investment
        for b in bucket_allocations
        if b.future_investment is not None
    ]

    _attach_rationales(bucket_allocations)

    all_mult = all(
        float(v).is_integer() and int(v) % 100 == 0
        for row in step5.rows
        for v in (
            row.emergency,
            row.short_term,
            row.medium_term,
            row.long_term,
            row.total,
        )
    )

    asset_class_breakdown = _asset_class_breakdown(
        inp, step1, step2, step4, step5.grand_total
    )

    return GoalAllocationOutput(
        client_summary=client_summary,
        bucket_allocations=bucket_allocations,
        aggregated_subgroups=aggregated_subgroups,
        future_investments_summary=future_investments_summary,
        grand_total=step5.grand_total,
        all_amounts_in_multiples_of_100=all_mult,
        asset_class_breakdown=asset_class_breakdown,
        goal_funding=step2.goal_funding,
    )
