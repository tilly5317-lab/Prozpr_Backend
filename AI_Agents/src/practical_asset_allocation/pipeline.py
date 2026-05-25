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
)


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
    # Tasks 6-10 will add: allocation_2_*, equities_amount, debt_amount,
    # others_amount, non_mf_equity_actual, excess_direct_stocks,
    # residual_equity_corpus, multi_asset block, equity_subgroup_amounts,
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

    return _PracticalLongTermResult(
        total_long_term_corpus=total_long_term_corpus,
        min_equity_elss_pct=min_equity_elss_pct,
        phase1_bounds_allocation_1=bounds_1,
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
