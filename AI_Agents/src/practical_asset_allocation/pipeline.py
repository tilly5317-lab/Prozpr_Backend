"""practical_asset_allocation pipeline — see module __init__ docstring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

from asset_allocation_pydantic.models import (
    AggregatedRow,
    AggregatedSubgroupRow,
    AllocationInput,
    AssetClassAllocation,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAllocation,
    BucketAssetClassSplit,
    ClientSummary,
    FutureInvestment,
    Goal,
    MultiAssetBlock,
    Step1Output,
    Step2Output,
    Step3Output,
    Step4Output,
    Step5Output,
)
from asset_allocation_pydantic.steps import (
    step1_emergency,
    step2_short_term,
    step3_medium_term,
    step5_aggregation,
)
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
    phase2_asset_class_pcts,
    phase4_multi_asset,
    phase5_equity_subgroups,
)
from asset_allocation_pydantic.tables import (
    EQUITY_SUBGROUPS,
    LONG_TERM_BOUNDARY_MONTHS,
    STEP4_SUBGROUPS,
    SUBGROUP_TO_ASSET_CLASS,
)
from asset_allocation_pydantic.utils import round_to_100


# Spec §B.5 step 4 — practical-side others-gate (stricter than upstream).
# Upstream uses score >= 8 AND view <= 6; practical uses score > 8 AND view < 7.
PRACTICAL_OTHERS_GATE_SCORE_THRESHOLD: float = 8.0
PRACTICAL_OTHERS_GATE_VIEW_THRESHOLD: float = 7.0

# Spec §B.5 step 7 (R182) — NFA-banded max non-MF equity %.
# > 5Cr → 75%, > 2Cr → 60%, > 1Cr → 50%, else → 33%.
NFA_BAND_5CR_INR: float = 50_000_000.0
NFA_BAND_2CR_INR: float = 20_000_000.0
NFA_BAND_1CR_INR: float = 10_000_000.0
NFA_BAND_PCT_ABOVE_5CR: float = 0.75
NFA_BAND_PCT_ABOVE_2CR: float = 0.60
NFA_BAND_PCT_ABOVE_1CR: float = 0.50
NFA_BAND_PCT_DEFAULT: float = 0.33

# Spec §B.5 step 9 (R198-R199) — v2 average-based slider.
# min_equity_pct_required = max(8 - max(0, locked_share - 0.20) * 10, min(3, avg))
SLIDER_BASE_PCT: float = 8.0
SLIDER_LOCKED_THRESHOLD: float = 0.20
SLIDER_LOCKED_MULTIPLIER: float = 10.0
SLIDER_AVG_CAP_PCT: float = 3.0


def _nfa_banded_max_non_mf_equity_pct(nfa: Optional[float]) -> float:
    """R182: returns the NFA-banded max non-MF equity %. Treats None NFA as the
    bottom band (33%) — defensive: callers normally pass NFA always."""
    if nfa is None:
        return NFA_BAND_PCT_DEFAULT
    if nfa > NFA_BAND_5CR_INR:
        return NFA_BAND_PCT_ABOVE_5CR
    if nfa > NFA_BAND_2CR_INR:
        return NFA_BAND_PCT_ABOVE_2CR
    if nfa > NFA_BAND_1CR_INR:
        return NFA_BAND_PCT_ABOVE_1CR
    return NFA_BAND_PCT_DEFAULT


class InfeasibleGoalError(ValueError):
    """Raised when the input corpus cannot satisfy structural constraints
    (e.g. ELSS holdings exceed total corpus)."""


class PracticalAllocationInput(AllocationInput):
    """Extends AllocationInput with four holdings-aware corpus scalars.

    Implicit corpus accounting (not separate inputs):
      cash               = total_corpus - mf_corpus - non_mf_equity_corpus
      mf_non_elss        = mf_corpus - elss_corpus
      rebalancing_corpus = total_corpus - elss_corpus
    """

    mf_corpus: float = Field(..., ge=0)
    """Total MF holdings INCLUDING ELSS."""

    non_mf_equity_corpus: float = Field(default=0.0, ge=0)
    """Direct stocks + PMS — non-MF equity, treated separately because the
    rebalancing engine can't trade them per-fund."""

    elss_corpus: float = Field(default=0.0, ge=0)
    """ELSS MF holdings (subset of mf_corpus). Locked under 3-year SEBI
    lock-in — surfaced as a frozen long-term row."""

    max_non_mf_equity_pct_client_input: Optional[float] = Field(default=None)
    """Advisor override for the NFA-banded non-MF equity cap (Option A)."""


class CorpusBreakdown(BaseModel):
    """Practical-only block: how the customer's corpus splits across MF /
    non-MF equity / cash, and what the engine actually deployed.

    All amounts are rupees rounded to whole integers; the engine internally
    works in floats and rounds at the boundary.
    """
    total_corpus_inr: int = Field(..., ge=0)
    mf_corpus_inr: int = Field(..., ge=0)
    non_mf_equity_input_inr: int = Field(..., ge=0)
    """Echo of the input — what the customer said they hold."""
    elss_corpus_inr: int = Field(..., ge=0)
    rebalancing_corpus_inr: int = Field(..., ge=0)
    """total_corpus_inr - elss_corpus_inr (ELSS is frozen)."""
    non_mf_equity_actual_inr: int = Field(..., ge=0)
    """<= input, NFA-capped — what the engine could absorb."""
    excess_direct_stocks_inr: int = Field(..., ge=0)
    """input - actual; drives the SELL_DIRECT_STOCKS recommendation downstream."""
    max_non_mf_equity_pct_computed: float = Field(..., ge=0.0, le=1.0)
    """NFA-banded value used (or override if the advisor provided one)."""


class PracticalAllocationOutput(BaseModel):
    """Shape-parity with GoalAllocationOutput (same seven fields) plus one
    extras block (corpus_breakdown).

    Any consumer that already understands GoalAllocationOutput handles
    PracticalAllocationOutput for the shared seven fields with zero change.
    """
    client_summary: ClientSummary
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    """Same shape as GoalAllocationOutput.aggregated_subgroups, but includes
    two extra rows: 'tax_efficient_equities' (ELSS amount in long_term column)
    and 'non_mf_equities' (non-MF equity actual in long_term column)."""
    future_investments_summary: List[FutureInvestment]
    grand_total: float
    all_amounts_in_multiples_of_100: bool
    asset_class_breakdown: AssetClassBreakdown
    corpus_breakdown: CorpusBreakdown


@dataclass
class _PracticalLongTermResult:
    """Internal carrier for the long-term step output. Filled in across
    Tasks 5-10; output assembly (Tasks 11-12) reads from here."""
    # R157-R165 (Task 5):
    total_long_term_corpus: int
    min_equity_elss_pct: float
    phase1_bounds_allocation_1: ResolvedBounds
    # R167-R174 (Task 6):
    practical_others_gate_fired: bool
    allocation_2_equity_pct: int
    allocation_2_debt_pct: int
    allocation_2_others_pct: int
    # R177-R186 (Task 7):
    equities_amount: int
    debt_amount: int
    others_amount: int
    elss_amount_frozen: int
    max_non_mf_equity_pct_computed: float
    max_non_mf_equity_pct_considered: float
    max_equities_shares: int
    non_mf_equity_actual: int
    excess_direct_stocks: int
    residual_equity_corpus_pre_multi_asset: int
    # R187-R194 (Task 8):
    multi_asset_block: MultiAssetBlock
    multi_asset_others_excess: int
    excess_to_debt: int
    excess_to_equity: int
    residual_equity_corpus_final: int
    residual_debt_corpus: int
    # R196-R215 (Task 9):
    average_equity_subgroup_allocation_pct: float
    min_equity_pct_required: float
    equity_subgroup_amounts: dict[str, int]  # one entry per EQUITY_SUBGROUPS
    # R217-R222 (Task 10):
    residual_other_corpus: int
    long_term_subgroup_amounts: dict[str, int]  # one entry per STEP4_SUBGROUPS
    goals_allocated: List[Goal]
    future_investment: Optional[FutureInvestment]


def _run_practical_long_term(
    *,
    inp: AllocationInput,
    remaining_corpus: int,
    elss_amount: float,
    non_mf_equity_input: float,
    nfa: Optional[float],
    max_non_mf_equity_pct_client_input: Optional[float],
) -> _PracticalLongTermResult:
    """Long-term step — Excel R157-R222. Holdings-aware.

    Layout (split across Tasks 5-10):
      Task 5  (R157-R165): corpus assembly, ELSS floor, first-level bounds.
      Task 6  (R167-R174): others-gate, second-level allocation pct.
      Task 7  (R177-R186): amounts, ELSS, non-MF cap, residual_equity.
      Task 8  (R187-R194): multi-asset block.
      Task 9  (R196-R215): equity subgroup gates, slider, amounts.
      Task 10 (R217-R222): debt and others residuals.
    """
    # R-pre: filter long-term goals using LONG_TERM_BOUNDARY_MONTHS (same
    # operator as upstream step4_long_term.run). Emit FutureInvestment when
    # corpus is short of the goal sum (spec §B.7 edge case β).
    lt_goals = [
        g for g in inp.goals
        if g.time_to_goal_months >= LONG_TERM_BOUNDARY_MONTHS
    ]
    sum_goals = round_to_100(sum(g.amount_needed for g in lt_goals))
    future_investment: Optional[FutureInvestment] = None
    if sum_goals > remaining_corpus:
        future_investment = FutureInvestment(
            bucket="long_term",
            future_investment_amount=sum_goals - remaining_corpus,
        )

    # R158: long-term corpus includes ELSS added back (ELSS is locked but
    # counted toward the long-term equity-class budget).
    total_long_term_corpus = max(0, int(remaining_corpus + elss_amount))

    # R159: ELSS-as-floor share of long-term equity.
    if total_long_term_corpus > 0:
        min_equity_elss_pct = elss_amount / total_long_term_corpus
    else:
        min_equity_elss_pct = 0.0

    # R161-R165: first-level asset-class bounds from PHASE1_RISK_BOUNDS,
    # reused verbatim from asset_allocation_pydantic.
    bounds_1 = phase1_bounds(
        score=inp.effective_risk_score,
        market_commentary=inp.market_commentary,
        goals=[],  # phase1_bounds does not use goals; pass empty for now.
        intergenerational_transfer=inp.intergenerational_transfer,
    )

    # R167-R168: stricter practical others-gate. Note: phase1_bounds already
    # applied the upstream gate (score >= 8 AND view <= 6) inside bounds_1.
    # We layer the stricter variant (score > 8 AND view < 7) on top so the
    # practical engine zeros others slightly earlier than the ideal engine.
    practical_others_gate_fired = (
        inp.effective_risk_score > PRACTICAL_OTHERS_GATE_SCORE_THRESHOLD
        and inp.market_commentary.others < PRACTICAL_OTHERS_GATE_VIEW_THRESHOLD
    )
    bounds_for_phase2 = bounds_1
    if practical_others_gate_fired and (bounds_1.others_min > 0 or bounds_1.others_max > 0):
        # Pro-rata redistribute the zeroed others to equity and debt mins.
        freed_max = bounds_1.others_max
        freed_min = bounds_1.others_min
        eq_max_new = bounds_1.eq_max
        debt_max_new = bounds_1.debt_max
        eq_min_new = bounds_1.eq_min
        debt_min_new = bounds_1.debt_min
        total_max = bounds_1.eq_max + bounds_1.debt_max
        if total_max > 0 and freed_max > 0:
            eq_add = int(round(freed_max * bounds_1.eq_max / total_max))
            eq_max_new += eq_add
            debt_max_new += freed_max - eq_add
        total_min = bounds_1.eq_min + bounds_1.debt_min
        if total_min > 0 and freed_min > 0:
            eq_add_min = int(round(freed_min * bounds_1.eq_min / total_min))
            eq_min_new += eq_add_min
            debt_min_new += freed_min - eq_add_min
        bounds_for_phase2 = ResolvedBounds(
            eq_min=eq_min_new, eq_max=eq_max_new,
            debt_min=debt_min_new, debt_max=debt_max_new,
            others_min=0, others_max=0,
        )

    # R170: market-view tilt → phase2_asset_class_pcts (reused upstream).
    a2_eq_pct_raw, a2_debt_pct_raw, a2_oth_pct_raw = phase2_asset_class_pcts(
        bounds_for_phase2, inp.market_commentary,
    )

    # R171: ELSS floor lifts equity allocation if needed.
    elss_floor_pct_int = int(round(min_equity_elss_pct * 100))
    allocation_2_equity_pct = max(a2_eq_pct_raw, elss_floor_pct_int)

    # R172: pro-rata redistribution of the residual into debt / others.
    # Excel formula: (100-F171) * E172 / (E172+E173) where E172/E173 are the
    # allocation_1 tilted averages. We use the same denominator (the upstream
    # phase2 tilted raws), not the phase1 mins, so an asset class with mins=0
    # still receives its tilt-driven share.
    remaining_pct = 100 - allocation_2_equity_pct
    if remaining_pct <= 0:
        allocation_2_debt_pct = 0
        allocation_2_others_pct = 0
        # Force-clamp equity at 100 if the ELSS floor overshot.
        allocation_2_equity_pct = 100
    else:
        dt_oth_raw = a2_debt_pct_raw + a2_oth_pct_raw
        if dt_oth_raw > 0:
            allocation_2_debt_pct = int(round(
                remaining_pct * a2_debt_pct_raw / dt_oth_raw
            ))
            allocation_2_others_pct = remaining_pct - allocation_2_debt_pct
        else:
            # Degenerate: both raws zero. All residual → debt by default.
            allocation_2_debt_pct = remaining_pct
            allocation_2_others_pct = 0

    # R177-R179: amounts.
    equities_amount = round_to_100(
        total_long_term_corpus * allocation_2_equity_pct / 100
    )
    others_amount = round_to_100(
        total_long_term_corpus * allocation_2_others_pct / 100
    )
    debt_amount = max(0, total_long_term_corpus - equities_amount - others_amount)
    debt_amount = round_to_100(debt_amount)

    # Reconcile rounding drift onto the largest amount (mirrors upstream pattern).
    drift = total_long_term_corpus - (equities_amount + debt_amount + others_amount)
    if drift != 0:
        amounts_by_name = {"eq": equities_amount, "dt": debt_amount, "oth": others_amount}
        largest = max(amounts_by_name, key=lambda k: amounts_by_name[k])
        amounts_by_name[largest] += drift
        equities_amount = max(0, amounts_by_name["eq"])
        debt_amount = max(0, amounts_by_name["dt"])
        others_amount = max(0, amounts_by_name["oth"])

    # R180: ELSS frozen amount.
    elss_amount_frozen = int(round(elss_amount))

    # R182-R184: NFA-banded cap + advisor override (Option A — client wins).
    max_non_mf_equity_pct_computed = _nfa_banded_max_non_mf_equity_pct(nfa)
    max_non_mf_equity_pct_considered = (
        max_non_mf_equity_pct_client_input
        if max_non_mf_equity_pct_client_input is not None
        else max_non_mf_equity_pct_computed
    )

    # R185: ceiling for non-MF equity absorption.
    max_equities_shares = int(round(
        max_non_mf_equity_pct_considered * equities_amount
    ))

    # R186: non-MF actual = min(input, equities_amount - elss, max_equities_shares).
    available_after_elss = max(0, equities_amount - elss_amount_frozen)
    non_mf_equity_actual = int(round(min(
        non_mf_equity_input,
        available_after_elss,
        max_equities_shares,
    )))
    non_mf_equity_actual = max(0, non_mf_equity_actual)

    # Excess (drives SELL_DIRECT_STOCKS downstream in Rebalancing).
    excess_direct_stocks = max(
        0, int(round(non_mf_equity_input)) - non_mf_equity_actual,
    )

    # Residual equity corpus available for MF subgroups (pre-multi-asset).
    residual_equity_corpus_pre_multi_asset = max(
        0, equities_amount - non_mf_equity_actual - elss_amount_frozen,
    )

    # R187: multi-asset block. The upstream helper already caps the multi-asset
    # equity slice at MULTI_ASSET_EQUITY_CAP_PCT and rounds to 100. We feed it
    # the practical RESIDUAL equity (post-ELSS, post-non-MF) rather than
    # equities_amount, so the multi-asset cap respects what we can actually
    # deploy via MFs.
    multi_asset_block = phase4_multi_asset(
        equities_amount=residual_equity_corpus_pre_multi_asset,
        debt_amount=debt_amount,
        others_amount=others_amount,
        composition=inp.multi_asset_composition,
    )

    # R193: overflow redistribution. When the multi-asset others slice exceeds
    # the budgeted others_amount, the excess is split between equity (residual)
    # and debt (allocation_2_debt_pct-weighted, clamped to remaining debt
    # capacity after the multi-asset debt component).
    multi_asset_others_excess = max(
        0, multi_asset_block.others_component - others_amount,
    )
    debt_capacity_after_multi = max(
        0, debt_amount - multi_asset_block.debt_component,
    )
    if multi_asset_others_excess > 0 and (
        allocation_2_debt_pct + allocation_2_equity_pct
    ) > 0:
        # Spec wording: excess_to_debt = min(round_to_100(excess × allocation_2_debt
        # / 100), debt_amount − multi_asset_debt_component).
        excess_to_debt = min(
            round_to_100(multi_asset_others_excess * allocation_2_debt_pct / 100),
            debt_capacity_after_multi,
        )
        excess_to_equity = multi_asset_others_excess - excess_to_debt
    else:
        excess_to_debt = 0
        excess_to_equity = 0

    # R194: residual equity corpus AFTER multi-asset equity component AND the
    # excess-to-equity redirect.
    residual_equity_corpus_final = max(
        0,
        residual_equity_corpus_pre_multi_asset
        - multi_asset_block.equity_component
        - excess_to_equity,
    )

    # R217 (preview for Task 10): residual debt corpus after multi-asset debt
    # component AND the excess-to-debt redirect.
    residual_debt_corpus = max(
        0,
        debt_amount - multi_asset_block.debt_component - excess_to_debt,
    )

    # R196-R200: equity subgroup allocation via upstream phase5_equity_subgroups.
    # This already applies the sector/value view-<= 7 gates and the upstream
    # PHASE5_MIN_SUBGROUP_SHARE_PCT (2%) internal drop. We then layer the v2
    # average-based slider on top (R198-R199) and drop+renormalise.
    initial_subgroup_amounts = phase5_equity_subgroups(
        total_equity_for_subgroups=residual_equity_corpus_final,
        score=inp.effective_risk_score,
        market_commentary=inp.market_commentary,
    )

    # R198: per-subgroup % OF EQUITIES (the equity slice that funds the MF
    # subgroup pool — NOT total long-term equities_amount, since ELSS and
    # non-MF actual are NOT MF subgroup deployment).
    pct_of_equity_per_subgroup: dict[str, float] = {}
    if residual_equity_corpus_final > 0:
        for sg, amt in initial_subgroup_amounts.items():
            pct_of_equity_per_subgroup[sg] = (
                amt * 100.0 / residual_equity_corpus_final
            )
    else:
        pct_of_equity_per_subgroup = {sg: 0.0 for sg in initial_subgroup_amounts}

    non_zero_pcts = [pct for pct in pct_of_equity_per_subgroup.values() if pct > 0]
    average_equity_subgroup_allocation_pct = (
        sum(non_zero_pcts) / len(non_zero_pcts) if non_zero_pcts else 0.0
    )

    # R199 (v2 slider): with heavily-locked equity (ELSS + non-MF actual >
    # 20% of equities_amount), allow a lower-than-8% threshold; cap the lower
    # bound at min(3, average_subgroup_allocation).
    if equities_amount > 0:
        locked_share = (
            elss_amount_frozen + non_mf_equity_actual
        ) / equities_amount
    else:
        locked_share = 0.0
    first_term = (
        SLIDER_BASE_PCT
        - max(0.0, locked_share - SLIDER_LOCKED_THRESHOLD)
        * SLIDER_LOCKED_MULTIPLIER
    )
    second_term = min(SLIDER_AVG_CAP_PCT, average_equity_subgroup_allocation_pct)
    min_equity_pct_required = max(first_term, second_term)

    # R200-R215: drop subgroups below the slider threshold; redistribute
    # proportionally over survivors; convert back to amounts.
    surviving = {
        sg: amt for sg, amt in initial_subgroup_amounts.items()
        if pct_of_equity_per_subgroup.get(sg, 0.0) >= min_equity_pct_required
    }
    dropped_total = sum(
        amt for sg, amt in initial_subgroup_amounts.items()
        if sg not in surviving
    )
    surviving_sum = sum(surviving.values())
    if surviving_sum > 0 and dropped_total > 0:
        renormalised = {
            sg: round_to_100(amt + dropped_total * amt / surviving_sum)
            for sg, amt in surviving.items()
        }
    else:
        renormalised = dict(surviving)

    # Pad with zeros for the dropped subgroups so the result dict shape stays
    # exhaustive over EQUITY_SUBGROUPS.
    equity_subgroup_amounts: dict[str, int] = {sg: 0 for sg in EQUITY_SUBGROUPS}
    for sg, amt in renormalised.items():
        equity_subgroup_amounts[sg] = amt

    # Reconcile any residual rounding drift against residual_equity_corpus_final.
    drift = residual_equity_corpus_final - sum(equity_subgroup_amounts.values())
    if drift != 0 and any(v > 0 for v in equity_subgroup_amounts.values()):
        largest_sg = max(
            equity_subgroup_amounts, key=lambda k: equity_subgroup_amounts[k],
        )
        equity_subgroup_amounts[largest_sg] = max(
            0, equity_subgroup_amounts[largest_sg] + drift,
        )

    # R220-R222: gold / commodities = others budget minus what the multi-asset
    # fund's own others slice already absorbed (less any excess we already
    # redistributed to eq/debt).
    others_minus_multi = max(
        0,
        others_amount - (
            multi_asset_block.others_component - multi_asset_others_excess
        ),
    )
    residual_other_corpus = round_to_100(others_minus_multi)

    # R217-R219: assemble the long-term subgroup_amounts dict, exhaustive
    # over STEP4_SUBGROUPS.
    long_term_subgroup_amounts: dict[str, int] = {sg: 0 for sg in STEP4_SUBGROUPS}
    long_term_subgroup_amounts["multi_asset"] = multi_asset_block.multi_asset_amount
    for sg, amt in equity_subgroup_amounts.items():
        long_term_subgroup_amounts[sg] = amt
    # Spec §B.5 step 11: long-term debt residual ALWAYS routes to
    # arbitrage_plus_income; the tax-rate gate on debt routing applies to
    # medium-term only (asset_allocation Part A.4).
    long_term_subgroup_amounts["arbitrage_plus_income"] = residual_debt_corpus
    long_term_subgroup_amounts["short_debt"] = 0  # explicit zero
    long_term_subgroup_amounts["gold_commodities"] = residual_other_corpus

    return _PracticalLongTermResult(
        total_long_term_corpus=total_long_term_corpus,
        min_equity_elss_pct=min_equity_elss_pct,
        phase1_bounds_allocation_1=bounds_1,
        practical_others_gate_fired=practical_others_gate_fired,
        allocation_2_equity_pct=allocation_2_equity_pct,
        allocation_2_debt_pct=allocation_2_debt_pct,
        allocation_2_others_pct=allocation_2_others_pct,
        equities_amount=equities_amount,
        debt_amount=debt_amount,
        others_amount=others_amount,
        elss_amount_frozen=elss_amount_frozen,
        max_non_mf_equity_pct_computed=max_non_mf_equity_pct_computed,
        max_non_mf_equity_pct_considered=max_non_mf_equity_pct_considered,
        max_equities_shares=max_equities_shares,
        non_mf_equity_actual=non_mf_equity_actual,
        excess_direct_stocks=excess_direct_stocks,
        residual_equity_corpus_pre_multi_asset=residual_equity_corpus_pre_multi_asset,
        multi_asset_block=multi_asset_block,
        multi_asset_others_excess=multi_asset_others_excess,
        excess_to_debt=excess_to_debt,
        excess_to_equity=excess_to_equity,
        residual_equity_corpus_final=residual_equity_corpus_final,
        residual_debt_corpus=residual_debt_corpus,
        average_equity_subgroup_allocation_pct=average_equity_subgroup_allocation_pct,
        min_equity_pct_required=min_equity_pct_required,
        equity_subgroup_amounts=equity_subgroup_amounts,
        residual_other_corpus=residual_other_corpus,
        long_term_subgroup_amounts=long_term_subgroup_amounts,
        goals_allocated=lt_goals,
        future_investment=future_investment,
    )


def run_practical_allocation(inp: PracticalAllocationInput) -> PracticalAllocationOutput:
    """Holdings-aware goal-based allocation. Spec §B.4.

    Pipeline:
      1. ELSS freeze — subtract elss_corpus to get rebalancing_corpus.
      2. Build sub-AllocationInput with total_corpus = rebalancing_corpus.
      3. Run upstream steps 1-3 (emergency, short-term, medium-term) verbatim.
      4. Run _run_practical_long_term (Excel R157-R222) for the long-term step.
      5. Aggregate with step5_aggregation_with_frozen (adds two frozen rows).
      6. Assemble PracticalAllocationOutput.
    """
    rebalancing_corpus = inp.total_corpus - inp.elss_corpus
    if rebalancing_corpus < 0:
        # Edge case (α) per spec §B.7 — should never happen in practice.
        raise InfeasibleGoalError(
            f"ELSS corpus ({inp.elss_corpus}) exceeds total corpus ({inp.total_corpus})"
        )

    # Build a sub-AllocationInput with rebalancing_corpus as total_corpus.
    # model_dump() preserves all parent fields; we override total_corpus only.
    parent_fields = AllocationInput.model_fields.keys()
    sub_inp = AllocationInput(
        **{k: getattr(inp, k) for k in parent_fields if k != "total_corpus"},
        total_corpus=rebalancing_corpus,
    )

    s1 = step1_emergency.run(sub_inp)
    s2 = step2_short_term.run(sub_inp, s1.remaining_corpus)
    s3 = step3_medium_term.run(sub_inp, s2.remaining_corpus)

    s4_practical = _run_practical_long_term(
        inp=sub_inp,
        remaining_corpus=s3.remaining_corpus,
        elss_amount=inp.elss_corpus,
        non_mf_equity_input=inp.non_mf_equity_corpus,
        nfa=inp.net_financial_assets,
        max_non_mf_equity_pct_client_input=inp.max_non_mf_equity_pct_client_input,
    )

    s5 = _step5_aggregation_with_frozen(
        total_corpus=inp.total_corpus,
        s1=s1, s2=s2, s3=s3, s4_practical=s4_practical,
        elss_amount=inp.elss_corpus,
        non_mf_equity_actual=s4_practical.non_mf_equity_actual,
    )

    return _build_output(inp, s1, s2, s3, s4_practical, s5)


def _adapt_practical_to_step4_output(
    s4_practical: _PracticalLongTermResult,
) -> Step4Output:
    """Build a Step4Output whose subgroup_amounts is the practical long-term
    distribution. asset_allocation_pydantic.step5_aggregation only reads
    .subgroup_amounts on the step4 input, so the other fields are best-effort
    placeholders. We construct minimal valid pydantic objects."""
    zero_alloc = AssetClassAllocation(
        equities_pct=0, debt_pct=0, others_pct=0,
        equities_amount=s4_practical.equities_amount,
        debt_amount=s4_practical.debt_amount,
        others_amount=s4_practical.others_amount,
    )
    return Step4Output(
        asset_class_allocation=zero_alloc,
        planned_asset_class_allocation=zero_alloc,
        planned_subgroup_amounts=s4_practical.long_term_subgroup_amounts,
        multi_asset=s4_practical.multi_asset_block,
        goals_allocated=s4_practical.goals_allocated,
        leftover_corpus=0,
        total_long_term_corpus=s4_practical.total_long_term_corpus,
        total_allocated=sum(s4_practical.long_term_subgroup_amounts.values()),
        remaining_corpus=0,
        future_investment=s4_practical.future_investment,
        subgroup_amounts=s4_practical.long_term_subgroup_amounts,
    )


def _step5_aggregation_with_frozen(
    *,
    total_corpus: float,
    s1: Step1Output,
    s2: Step2Output,
    s3: Step3Output,
    s4_practical: _PracticalLongTermResult,
    elss_amount: float,
    non_mf_equity_actual: int,
) -> Step5Output:
    """Wraps upstream step5_aggregation.run and appends two frozen subgroup
    rows: tax_efficient_equities (ELSS) and non_mf_equities (non-MF actual).

    grand_total reconciles to total_corpus (NOT rebalancing_corpus) because
    the two frozen rows make ELSS and non-MF actual visible.
    """
    s4_adapter = _adapt_practical_to_step4_output(s4_practical)
    # Call upstream against total_corpus, not rebalancing_corpus, so the
    # match-flag uses the correct denominator. The upstream function does not
    # subtract anything; it just sums the four bucket dicts.
    base = step5_aggregation.run(total_corpus, s1, s2, s3, s4_adapter)

    rows = list(base.rows)
    elss_int = int(round(elss_amount))
    if elss_int > 0:
        rows.append(AggregatedRow(
            subgroup="tax_efficient_equities",
            emergency=0, short_term=0, medium_term=0,
            long_term=elss_int, total=elss_int,
        ))
    if non_mf_equity_actual > 0:
        rows.append(AggregatedRow(
            subgroup="non_mf_equities",
            emergency=0, short_term=0, medium_term=0,
            long_term=non_mf_equity_actual, total=non_mf_equity_actual,
        ))

    grand_total = sum(row.total for row in rows)
    grand_total_matches_corpus = abs(
        grand_total - round_to_100(total_corpus)
    ) <= 500

    return Step5Output(
        rows=rows,
        grand_total=grand_total,
        grand_total_matches_corpus=grand_total_matches_corpus,
    )


def _build_asset_class_breakdown(
    s1: Step1Output,
    s2: Step2Output,
    s3: Step3Output,
    s4_practical: _PracticalLongTermResult,
) -> AssetClassBreakdown:
    """Roll up subgroup amounts to (equity, debt, others) per bucket and
    overall. Mirrors what step7_presentation does in asset_allocation_pydantic
    but inlined here so we don't pull in that file's LLM rationale plumbing.

    tax_efficient_equities and non_mf_equities are added as equity in the
    long_term bucket via the practical-side rollup.
    """
    # Long-term: include the frozen ELSS + non-MF as equity (they ARE equity
    # exposure, just not via MF subgroups in the allocation_pydantic dict).
    lt_subs = dict(s4_practical.long_term_subgroup_amounts)
    lt_subs["tax_efficient_equities"] = s4_practical.elss_amount_frozen
    lt_subs["non_mf_equities"] = s4_practical.non_mf_equity_actual

    bucket_dicts = {
        "emergency": s1.subgroup_amounts,
        "short_term": s2.subgroup_amounts,
        "medium_term": s3.subgroup_amounts,
        "long_term": lt_subs,
    }

    # SUBGROUP_TO_ASSET_CLASS doesn't have the two practical-only subgroups;
    # add them locally as equity.
    extended_map = dict(SUBGROUP_TO_ASSET_CLASS)
    extended_map["tax_efficient_equities"] = "equity"
    extended_map["non_mf_equities"] = "equity"

    def split_with(subs: dict[str, int]) -> tuple[int, int, int]:
        eq = dt = oth = 0
        for sg, amt in subs.items():
            cls = extended_map.get(sg, "others")
            if cls == "equity":
                eq += amt
            elif cls == "debt":
                dt += amt
            else:
                oth += amt
        return eq, dt, oth

    per_bucket: List[BucketAssetClassSplit] = []
    for bucket_name, subs in bucket_dicts.items():
        eq, dt, oth = split_with(subs)
        tot = eq + dt + oth
        per_bucket.append(BucketAssetClassSplit(
            bucket=bucket_name,  # type: ignore[arg-type]
            equity=eq, debt=dt, others=oth,
            equity_pct=(eq * 100.0 / tot) if tot else 0.0,
            debt_pct=(dt * 100.0 / tot) if tot else 0.0,
            others_pct=(oth * 100.0 / tot) if tot else 0.0,
        ))

    eq_total = sum(b.equity for b in per_bucket)
    dt_total = sum(b.debt for b in per_bucket)
    oth_total = sum(b.others for b in per_bucket)
    grand = eq_total + dt_total + oth_total

    block = AssetClassSplitBlock(
        per_bucket=per_bucket,
        equity_total=eq_total, debt_total=dt_total, others_total=oth_total,
        equity_total_pct=(eq_total * 100.0 / grand) if grand else 0.0,
        debt_total_pct=(dt_total * 100.0 / grand) if grand else 0.0,
        others_total_pct=(oth_total * 100.0 / grand) if grand else 0.0,
    )

    return AssetClassBreakdown(
        planned=block,
        recommended=block,  # practical engine has no separate planned/recommended split
        recommended_sum_matches_grand_total=True,
        subgroups=None,
    )


def _build_output(
    inp: PracticalAllocationInput,
    s1: Step1Output,
    s2: Step2Output,
    s3: Step3Output,
    s4_practical: _PracticalLongTermResult,
    s5: Step5Output,
) -> PracticalAllocationOutput:
    """Assemble the seven shared fields + corpus_breakdown."""

    # 1. client_summary
    client_summary = ClientSummary(
        age=inp.age,
        occupation=inp.occupation_type,
        effective_risk_score=inp.effective_risk_score,
        total_corpus=inp.total_corpus,
        goals=inp.goals,
        emergency_fund_months=s1.emergency_fund_months,
        monthly_household_expense=inp.monthly_household_expense,
    )

    # 2. bucket_allocations
    emergency_bucket = BucketAllocation(
        bucket="emergency",
        goals=[],
        total_goal_amount=s1.total_emergency,
        allocated_amount=s1.total_emergency,
        future_investment=s1.future_investment,
        subgroup_amounts=s1.subgroup_amounts,
    )
    short_bucket = BucketAllocation(
        bucket="short_term",
        goals=s2.goals_allocated,
        total_goal_amount=s2.total_goal_amount,
        allocated_amount=s2.allocated_amount,
        future_investment=s2.future_investment,
        subgroup_amounts=s2.subgroup_amounts,
    )
    medium_bucket = BucketAllocation(
        bucket="medium_term",
        goals=[],  # MediumTermGoalAllocation is not the Goal type; keep empty
        total_goal_amount=s3.total_goal_amount,
        allocated_amount=s3.allocated_amount,
        future_investment=s3.future_investment,
        subgroup_amounts=s3.subgroup_amounts,
    )
    long_bucket = BucketAllocation(
        bucket="long_term",
        goals=s4_practical.goals_allocated,
        total_goal_amount=round_to_100(
            sum(g.amount_needed for g in s4_practical.goals_allocated),
        ),
        allocated_amount=sum(s4_practical.long_term_subgroup_amounts.values()),
        future_investment=s4_practical.future_investment,
        subgroup_amounts=s4_practical.long_term_subgroup_amounts,
    )

    # 3. aggregated_subgroups — convert Step5Output.rows to AggregatedSubgroupRow.
    aggregated = [
        AggregatedSubgroupRow(
            subgroup=row.subgroup,
            emergency=float(row.emergency),
            short_term=float(row.short_term),
            medium_term=float(row.medium_term),
            long_term=float(row.long_term),
            total=float(row.total),
        )
        for row in s5.rows
    ]

    # 4. future_investments_summary
    future_summary: List[FutureInvestment] = []
    for step_out in (s1, s2, s3):
        if step_out.future_investment is not None:
            future_summary.append(step_out.future_investment)
    if s4_practical.future_investment is not None:
        future_summary.append(s4_practical.future_investment)

    # 5. grand_total, 6. all_amounts_in_multiples_of_100
    grand_total = float(s5.grand_total)
    all_mult_100 = all(
        v % 100 == 0
        for d in (
            s1.subgroup_amounts, s2.subgroup_amounts,
            s3.subgroup_amounts, s4_practical.long_term_subgroup_amounts,
        )
        for v in d.values()
    )

    # 7. asset_class_breakdown
    asset_class_breakdown = _build_asset_class_breakdown(
        s1, s2, s3, s4_practical,
    )

    # corpus_breakdown extras
    corpus_breakdown = CorpusBreakdown(
        total_corpus_inr=int(round(inp.total_corpus)),
        mf_corpus_inr=int(round(inp.mf_corpus)),
        non_mf_equity_input_inr=int(round(inp.non_mf_equity_corpus)),
        elss_corpus_inr=int(round(inp.elss_corpus)),
        rebalancing_corpus_inr=int(round(inp.total_corpus - inp.elss_corpus)),
        non_mf_equity_actual_inr=s4_practical.non_mf_equity_actual,
        excess_direct_stocks_inr=s4_practical.excess_direct_stocks,
        max_non_mf_equity_pct_computed=s4_practical.max_non_mf_equity_pct_considered,
    )

    return PracticalAllocationOutput(
        client_summary=client_summary,
        bucket_allocations=[emergency_bucket, short_bucket, medium_bucket, long_bucket],
        aggregated_subgroups=aggregated,
        future_investments_summary=future_summary,
        grand_total=grand_total,
        all_amounts_in_multiples_of_100=all_mult_100,
        asset_class_breakdown=asset_class_breakdown,
        corpus_breakdown=corpus_breakdown,
    )
