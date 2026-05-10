from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models import (
    AllocationInput,
    AssetClassAllocation,
    ElssBlock,
    FutureInvestment,
    Goal,
    MarketCommentaryScores,
    MultiAssetBlock,
    MultiAssetFundComposition,
    Step4Output,
)
from ..tables import (
    ASSET_CLASS_RECONCILIATION_TOLERANCE,
    CLAMP_EPSILON,
    CLAMP_MAX_ITER,
    EQUITY_SUBGROUPS,
    INTERGEN_SCORE_BOOST,
    INTERGEN_SCORE_CAP,
    LONG_TERM_BOUNDARY_MONTHS,
    MARKET_VIEW_CENTER,
    MARKET_VIEW_HALF_RANGE,
    MIN_EQUITY_SUBGROUP_SHARE_PCT,
    MULTI_ASSET_EQUITY_CAP_PCT,
    OTHERS_GATE_MARKET_VIEW_THRESHOLD,
    OTHERS_GATE_SCORE_THRESHOLD,
    PHASE1_RISK_BOUNDS,
    PHASE5_EQUITY_SUBGROUP_BOUNDS,
    PHASE5_MARKET_VIEW_GATES,
    PHASE5_MIN_SUBGROUP_SHARE_PCT,
    SECTION_80C_LIMIT,
    STEP4_SUBGROUPS,
    TAX_RATE_MEDIUM_LONG_ARBITRAGE_THRESHOLD,
)
from ..utils import ceil_to_half, round_to_100


# ── Phase 1 — asset class min/max with overrides ──────────────────────────────


@dataclass
class ResolvedBounds:
    eq_min: int
    eq_max: int
    debt_min: int
    debt_max: int
    others_min: int
    others_max: int


def phase1_bounds(
    score: float,
    market_commentary: MarketCommentaryScores,
    goals: list[Goal],
    intergenerational_transfer: bool,
) -> ResolvedBounds:
    lookup_score = ceil_to_half(score)
    row = PHASE1_RISK_BOUNDS[lookup_score]

    eq_min, eq_max = row.eq_min, row.eq_max
    debt_min, debt_max = row.debt_min, row.debt_max
    others_min, others_max = row.others_min, row.others_max

    # Intergenerational transfer override: raise floors using adjusted score row.
    if intergenerational_transfer:
        adj = min(lookup_score + INTERGEN_SCORE_BOOST, INTERGEN_SCORE_CAP)
        adj = min(10.0, max(1.0, adj))
        adj_row = PHASE1_RISK_BOUNDS[adj]
        eq_min = max(eq_min, adj_row.eq_min)
        debt_min = adj_row.debt_min
        others_min = adj_row.others_min
        # Keep original maxes from lookup_score row (already set above).

    # Others gate: at high risk with tepid others view, zero out others.
    if (
        lookup_score >= OTHERS_GATE_SCORE_THRESHOLD
        and market_commentary.others <= OTHERS_GATE_MARKET_VIEW_THRESHOLD
    ):
        freed_max = others_max
        freed_min = others_min
        others_min = 0
        others_max = 0
        # Redistribute freed max across equity/debt proportionally to their current max.
        total_max_now = eq_max + debt_max
        if total_max_now > 0 and freed_max > 0:
            eq_add = int(round(freed_max * eq_max / total_max_now))
            dt_add = freed_max - eq_add
            eq_max += eq_add
            debt_max += dt_add
        # Similarly re-home the freed min if any, proportional to existing mins.
        total_min_now = eq_min + debt_min
        if total_min_now > 0 and freed_min > 0:
            eq_add_min = int(round(freed_min * eq_min / total_min_now))
            dt_add_min = freed_min - eq_add_min
            eq_min += eq_add_min
            debt_min += dt_add_min

    return ResolvedBounds(
        eq_min=eq_min, eq_max=eq_max,
        debt_min=debt_min, debt_max=debt_max,
        others_min=others_min, others_max=others_max,
    )


# ── Phase 2 — proportional scaling to 100% ────────────────────────────────────


def _clamp_and_redistribute(
    scaled: list[float],
    mins: list[float],
    maxs: list[float],
    max_iter: int = CLAMP_MAX_ITER,
) -> list[float]:
    """Clamp values to [min, max] and redistribute excess/deficit across unclamped."""
    values = list(scaled)
    for _ in range(max_iter):
        # Identify which values are clamped at bounds.
        clamped_high = [i for i, v in enumerate(values) if v > maxs[i] + CLAMP_EPSILON]
        clamped_low = [i for i, v in enumerate(values) if v < mins[i] - CLAMP_EPSILON]
        if not clamped_high and not clamped_low:
            return values

        delta = 0.0
        for i in clamped_high:
            delta += values[i] - maxs[i]
            values[i] = float(maxs[i])
        for i in clamped_low:
            delta -= mins[i] - values[i]
            values[i] = float(mins[i])

        # Redistribute delta across unclamped values proportionally to their current value.
        unclamped = [i for i in range(len(values)) if i not in clamped_high and i not in clamped_low]
        if not unclamped:
            return values
        u_sum = sum(values[i] for i in unclamped)
        if u_sum > 0:
            for i in unclamped:
                values[i] += delta * values[i] / u_sum
        else:
            share = delta / len(unclamped)
            for i in unclamped:
                values[i] += share

    return values


def phase2_asset_class_pcts(
    bounds: ResolvedBounds,
    market_commentary: MarketCommentaryScores,
) -> tuple[int, int, int]:
    """Return (equities_pct, debt_pct, others_pct) — integers summing to 100."""
    mins = [float(bounds.eq_min), float(bounds.debt_min), float(bounds.others_min)]
    maxs = [float(bounds.eq_max), float(bounds.debt_max), float(bounds.others_max)]
    views = [market_commentary.equities, market_commentary.debt, market_commentary.others]

    raws: list[float] = []
    for mn, mx, view in zip(mins, maxs, views):
        midpoint = (mn + mx) / 2
        half = (mx - mn) / 2
        normalized = (view - MARKET_VIEW_CENTER) / MARKET_VIEW_HALF_RANGE
        raws.append(midpoint + normalized * half)

    raws = [max(0.0, r) for r in raws]
    total_raw = sum(raws)
    if total_raw <= 0:
        # Fallback: use mins (they must sum ≤ 100 by spec); pad with max_index.
        scaled = list(mins)
    else:
        scaled = [r * 100 / total_raw for r in raws]

    scaled = _clamp_and_redistribute(scaled, mins, maxs)

    ints = [int(round(v)) for v in scaled]
    diff = 100 - sum(ints)
    if diff != 0:
        # Adjust the largest (by scaled value) by the diff.
        idx = max(range(3), key=lambda i: scaled[i])
        ints[idx] += diff

    return ints[0], ints[1], ints[2]


# ── Phase 3 — ELSS first-pass ─────────────────────────────────────────────────


def phase3_elss(
    equities_amount: int,
    tax_regime: Literal["old", "new"],
    section_80c_utilized: float,
) -> ElssBlock:
    applicable = tax_regime == "old" and section_80c_utilized < SECTION_80C_LIMIT
    if not applicable:
        return ElssBlock(
            applicable=False,
            elss_headroom=None,
            elss_amount=0,
            residual_equity_corpus=equities_amount,
        )
    headroom = SECTION_80C_LIMIT - int(section_80c_utilized)
    elss_amount = round_to_100(min(headroom, equities_amount))
    residual = max(0, equities_amount - elss_amount)
    return ElssBlock(
        applicable=True,
        elss_headroom=headroom,
        elss_amount=elss_amount,
        residual_equity_corpus=residual,
    )


# ── Phase 4 — multi-asset fund decomposition ──────────────────────────────────


def phase4_multi_asset(
    residual_equity_corpus: int,
    debt_amount: int,
    others_amount: int,
    composition: MultiAssetFundComposition,
) -> MultiAssetBlock:
    eq_pct = composition.equity_pct / 100.0
    dt_pct = composition.debt_pct / 100.0
    oth_pct = composition.others_pct / 100.0

    INF = float("inf")
    max_x_eq = (
        (MULTI_ASSET_EQUITY_CAP_PCT * residual_equity_corpus) / eq_pct if eq_pct > 0 else INF
    )
    max_x_dt = debt_amount / dt_pct if dt_pct > 0 else INF

    candidate = min(max_x_eq, max_x_dt)
    if candidate == INF or candidate <= 0 or residual_equity_corpus <= 0 or debt_amount <= 0:
        multi_asset_amount = 0
    else:
        multi_asset_amount = round_to_100(candidate)

    equity_component = round_to_100(multi_asset_amount * eq_pct)
    debt_component = round_to_100(multi_asset_amount * dt_pct)
    others_component = round_to_100(multi_asset_amount * oth_pct)

    # If the multi-asset fund's others slice exceeds the budgeted others_amount
    # (e.g. others_gate zeroed it), the excess is funded by shrinking the equity
    # subgroup pool — not by trimming the fund.
    overage = max(0, others_component - others_amount)
    equity_for_subgroups = round_to_100(max(0, residual_equity_corpus - equity_component - overage))
    debt_for_subgroups = round_to_100(max(0, debt_amount - debt_component))
    remaining_others_for_gold = round_to_100(max(0, others_amount - others_component))

    return MultiAssetBlock(
        multi_asset_amount=multi_asset_amount,
        equity_component=equity_component,
        debt_component=debt_component,
        others_component=others_component,
        equity_for_subgroups=equity_for_subgroups,
        debt_for_subgroups=debt_for_subgroups,
        remaining_others_for_gold=remaining_others_for_gold,
    )


# ── Phase 5 — equity subgroups ────────────────────────────────────────────────


def phase5_equity_subgroups(
    total_equity_for_subgroups: int,
    score: float,
    market_commentary: MarketCommentaryScores,
) -> dict[str, int]:
    result: dict[str, int] = {sg: 0 for sg in EQUITY_SUBGROUPS}
    if total_equity_for_subgroups <= 0:
        return result

    row = PHASE5_EQUITY_SUBGROUP_BOUNDS[ceil_to_half(score)]

    # Strict gates BEFORE any allocation math.
    active = list(EQUITY_SUBGROUPS)
    for sg, gate in PHASE5_MARKET_VIEW_GATES.items():
        if getattr(market_commentary, sg) <= gate and sg in active:
            active.remove(sg)
    # Also drop any subgroup whose max is 0 at this risk score — they cannot receive allocation.
    active = [sg for sg in active if row[sg][1] > 0]

    if not active:
        return result

    mins = [float(row[sg][0]) for sg in active]
    maxs = [float(row[sg][1]) for sg in active]

    # Feasibility: if sum of maxes < 100, scale maxes up so they sum to 100.
    if sum(maxs) < 100:
        factor = 100.0 / sum(maxs)
        maxs = [m * factor for m in maxs]
        mins = [min(m_, mx_) for m_, mx_ in zip(mins, maxs)]

    views = [getattr(market_commentary, sg) for sg in active]
    raws: list[float] = []
    for mn, mx, view in zip(mins, maxs, views):
        midpoint = (mn + mx) / 2
        half = (mx - mn) / 2
        normalized = (view - MARKET_VIEW_CENTER) / MARKET_VIEW_HALF_RANGE
        raws.append(max(0.0, midpoint + normalized * half))

    total_raw = sum(raws)
    if total_raw <= 0:
        scaled = list(mins)
    else:
        scaled = [r * 100 / total_raw for r in raws]

    scaled = _clamp_and_redistribute(scaled, mins, maxs)

    ints = [int(round(v)) for v in scaled]
    # Drop-below-threshold pass (repeat once so values freed in round 1 can be
    # re-filtered if their redistribution pushed another subgroup below the bar).
    for _ in range(2):
        small_idx = [i for i, v in enumerate(ints) if 0 < v < PHASE5_MIN_SUBGROUP_SHARE_PCT]
        if not small_idx:
            break
        freed = sum(ints[i] for i in small_idx)
        for i in small_idx:
            ints[i] = 0
        remaining = [i for i in range(len(ints)) if i not in small_idx and ints[i] > 0]
        if not remaining:
            break
        r_sum = sum(ints[i] for i in remaining)
        for i in remaining:
            ints[i] += int(round(freed * ints[i] / r_sum))

    # Sum-to-100 fix.
    diff = 100 - sum(ints)
    if diff != 0 and ints:
        idx = max(range(len(ints)), key=lambda i: ints[i])
        ints[idx] += diff

    # Convert to amounts.
    amounts = [round_to_100(total_equity_for_subgroups * p / 100) for p in ints]
    # Exact-sum fix: adjust largest amount by any residual.
    S = sum(amounts)
    residual = total_equity_for_subgroups - S
    if residual != 0 and amounts:
        idx = max(range(len(amounts)), key=lambda i: amounts[i])
        amounts[idx] += residual

    for sg, amt in zip(active, amounts):
        result[sg] = max(0, amt)

    return result


# ── Step 4 orchestration + invariants ─────────────────────────────────────────


def _drop_small_equity_subgroups(
    equity_subgroup_amounts: dict[str, int], equities_amount: int
) -> dict[str, int]:
    """Drop any equity subgroup whose amount is below MIN_EQUITY_SUBGROUP_SHARE_PCT
    of total long-term equity, redistributing the freed amount proportionally
    across the surviving subgroups. ELSS and multi-asset are not in this dict
    so they are naturally excluded from both the filter and the redistribution.
    """
    if equities_amount <= 0 or not equity_subgroup_amounts:
        return equity_subgroup_amounts

    threshold = equities_amount * MIN_EQUITY_SUBGROUP_SHARE_PCT / 100
    freed = 0
    working = dict(equity_subgroup_amounts)
    for sg, amt in list(working.items()):
        if 0 < amt < threshold:
            freed += amt
            working[sg] = 0

    if freed == 0:
        return working

    surviving_sum = sum(working.values())
    target_total = surviving_sum + freed
    if surviving_sum == 0:
        return working  # nothing left to redistribute into

    redistributed = {
        sg: round_to_100(amt + freed * amt / surviving_sum) if amt > 0 else 0
        for sg, amt in working.items()
    }
    drift = target_total - sum(redistributed.values())
    if drift != 0:
        largest = max(redistributed, key=lambda k: redistributed[k])
        redistributed[largest] += drift
    return redistributed


def run(inp: AllocationInput, remaining_corpus: int) -> Step4Output:
    lt_goals = [g for g in inp.goals if g.time_to_goal_months > LONG_TERM_BOUNDARY_MONTHS]
    sum_goals = round_to_100(sum(g.amount_needed for g in lt_goals))

    if sum_goals > remaining_corpus:
        negotiable = [g.goal_name for g in lt_goals if g.goal_priority == "negotiable"]
        negotiable_str = ", ".join(negotiable) if negotiable else "none flagged"
        future_investment = FutureInvestment(
            bucket="long_term",
            future_investment_amount=sum_goals - remaining_corpus,
            message=(
                f"Your long-term goals ask for more than your current corpus "
                f"alone can provide today. The balance is wealth your monthly "
                f"investments will compound into over the years ahead — and "
                f"long-term is precisely where disciplined investing has the "
                f"biggest impact. Sticking with your SIPs, or flexing "
                f"negotiable goals ({negotiable_str}), makes all of these "
                f"firmly reachable."
            ),
        )
        total_long_term_corpus = remaining_corpus
        leftover_corpus = 0
    else:
        future_investment = None
        total_long_term_corpus = remaining_corpus
        leftover_corpus = remaining_corpus - sum_goals

    bounds = phase1_bounds(
        inp.effective_risk_score,
        inp.market_commentary,
        lt_goals,
        inp.intergenerational_transfer,
    )
    eq_pct, dt_pct, oth_pct = phase2_asset_class_pcts(bounds, inp.market_commentary)

    equities_amount = round_to_100(total_long_term_corpus * eq_pct / 100)
    debt_amount = round_to_100(total_long_term_corpus * dt_pct / 100)
    others_amount = round_to_100(total_long_term_corpus * oth_pct / 100)

    # Reconcile rounding drift so the three sum to total_long_term_corpus.
    drift = total_long_term_corpus - (equities_amount + debt_amount + others_amount)
    if drift != 0:
        amounts_by_name = {
            "eq": equities_amount, "dt": debt_amount, "oth": others_amount,
        }
        largest = max(amounts_by_name, key=lambda k: amounts_by_name[k])
        amounts_by_name[largest] += drift
        equities_amount = round_to_100(amounts_by_name["eq"])
        debt_amount = round_to_100(amounts_by_name["dt"])
        others_amount = round_to_100(amounts_by_name["oth"])

    elss = phase3_elss(equities_amount, inp.tax_regime, inp.section_80c_utilized)
    multi = phase4_multi_asset(
        elss.residual_equity_corpus, debt_amount, others_amount, inp.multi_asset_composition
    )
    planned_equity_subgroup_amounts = phase5_equity_subgroups(
        multi.equity_for_subgroups, inp.effective_risk_score, inp.market_commentary
    )
    equity_subgroup_amounts = _drop_small_equity_subgroups(
        planned_equity_subgroup_amounts, equities_amount
    )

    debt_key = (
        "arbitrage_plus_income"
        if inp.effective_tax_rate >= TAX_RATE_MEDIUM_LONG_ARBITRAGE_THRESHOLD
        else "debt_subgroup"
    )

    subgroup_amounts: dict[str, int] = {sg: 0 for sg in STEP4_SUBGROUPS}
    subgroup_amounts["tax_efficient_equities"] = elss.elss_amount
    subgroup_amounts["multi_asset"] = multi.multi_asset_amount
    for sg, amt in equity_subgroup_amounts.items():
        subgroup_amounts[sg] = amt
    subgroup_amounts[debt_key] = multi.debt_for_subgroups
    subgroup_amounts["gold_commodities"] = multi.remaining_others_for_gold

    total_allocated = sum(subgroup_amounts.values())

    # When the multi-asset fund's others slice outgrows the budgeted others_amount
    # (others_gate at high risk), reassign the overage from equities to others so
    # the reported asset-class split matches the money actually held.
    overage = max(0, multi.others_component - others_amount)
    eq_amt_final = equities_amount - overage
    oth_amt_final = others_amount + overage
    if total_long_term_corpus > 0:
        eq_pct_final = int(round(100 * eq_amt_final / total_long_term_corpus))
        oth_pct_final = int(round(100 * oth_amt_final / total_long_term_corpus))
        dt_pct_final = 100 - eq_pct_final - oth_pct_final
    else:
        eq_pct_final, dt_pct_final, oth_pct_final = eq_pct, dt_pct, oth_pct

    asset_class_allocation = AssetClassAllocation(
        equities_pct=eq_pct_final, debt_pct=dt_pct_final, others_pct=oth_pct_final,
        equities_amount=eq_amt_final, debt_amount=debt_amount, others_amount=oth_amt_final,
    )

    planned_asset_class_allocation = AssetClassAllocation(
        equities_pct=eq_pct, debt_pct=dt_pct, others_pct=oth_pct,
        equities_amount=equities_amount, debt_amount=debt_amount, others_amount=others_amount,
    )

    planned_subgroup_amounts: dict[str, int] = {sg: 0 for sg in STEP4_SUBGROUPS}
    planned_subgroup_amounts["tax_efficient_equities"] = elss.elss_amount
    planned_subgroup_amounts["multi_asset"] = multi.multi_asset_amount
    for sg, amt in planned_equity_subgroup_amounts.items():
        planned_subgroup_amounts[sg] = amt
    planned_subgroup_amounts[debt_key] = multi.debt_for_subgroups
    planned_subgroup_amounts["gold_commodities"] = multi.remaining_others_for_gold

    out = Step4Output(
        asset_class_allocation=asset_class_allocation,
        planned_asset_class_allocation=planned_asset_class_allocation,
        planned_subgroup_amounts=planned_subgroup_amounts,
        elss=elss,
        multi_asset=multi,
        goals_allocated=lt_goals,
        leftover_corpus=leftover_corpus,
        total_long_term_corpus=total_long_term_corpus,
        total_allocated=total_allocated,
        remaining_corpus=0,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )

    _verify_invariants(out)
    return out


def _verify_invariants(out: Step4Output) -> None:
    alloc = out.subgroup_amounts
    eq_sub_sum = sum(alloc[sg] for sg in EQUITY_SUBGROUPS)
    ac = out.asset_class_allocation
    m = out.multi_asset
    tol = ASSET_CLASS_RECONCILIATION_TOLERANCE

    assert ac.equities_pct + ac.debt_pct + ac.others_pct == 100, (
        f"asset class pcts sum to {ac.equities_pct + ac.debt_pct + ac.others_pct}, not 100"
    )
    assert eq_sub_sum == m.equity_for_subgroups, (
        f"equity subgroups sum {eq_sub_sum} != equity_for_subgroups {m.equity_for_subgroups}"
    )
    assert abs(eq_sub_sum + out.elss.elss_amount + m.equity_component - ac.equities_amount) <= tol, (
        f"equity reconciliation off by more than {tol}"
    )
    assert abs(m.debt_component + m.debt_for_subgroups - ac.debt_amount) <= tol
    others_gap = m.others_component + alloc["gold_commodities"] - ac.others_amount
    assert abs(others_gap) <= tol, f"others reconciliation off by more than {tol}: gap={others_gap}"
    assert sum(alloc.values()) == out.total_allocated
    for sg, v in alloc.items():
        assert v >= 0 and v % 100 == 0, f"{sg}={v} is not a non-negative multiple of 100"
