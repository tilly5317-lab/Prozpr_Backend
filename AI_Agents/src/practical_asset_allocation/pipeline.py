"""practical_asset_allocation pipeline — see module __init__ docstring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

from asset_allocation_pydantic.models import (
    AggregatedSubgroupRow,
    AllocationInput,
    AssetClassBreakdown,
    BucketAllocation,
    ClientSummary,
    FutureInvestment,
)
from asset_allocation_pydantic.steps import (
    step1_emergency,
    step2_short_term,
    step3_medium_term,
)
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
    phase2_asset_class_pcts,
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
    # Tasks 8-10 will add: multi_asset block, equity_subgroup_amounts,
    # subgroup_amounts, future_investment, goals_allocated, etc.


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

    # Tasks 11-12 implement step5_aggregation_with_frozen and _build_output.
    # Until then, this orchestrator can be unit-tested via monkeypatch of
    # _run_practical_long_term (see test_orchestrator_skeleton.py).
    raise NotImplementedError(
        "Output assembly lands in Tasks 11-12; the long-term path above "
        "is structurally complete and is exercised under monkeypatch."
    )
