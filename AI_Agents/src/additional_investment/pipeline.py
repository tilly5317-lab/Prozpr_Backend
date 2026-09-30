"""Pipeline orchestrator for additional-investment deployment. Pure-sync, DB-free."""

from __future__ import annotations

from math import ceil

from .models import (
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
    Cadence,
    FundBuy,
    SubgroupTarget,
)
from Rebalancing.config import SUBGROUP_FUND_COUNT_THRESHOLD_INR  # type: ignore[import-not-found]

from .ratio import compute_goal_first_targets
from .selection import select_funds


def _funds_per_subgroup(investable_corpus: float) -> int:
    """1 or 2 funds per subgroup by corpus — same threshold as rebalancing.

    Same NAME as Rebalancing.pipeline._funds_per_subgroup deliberately (same
    rule), but this takes the pre-subtracted investable corpus (1 arg) where the
    rebalancing one takes (total_corpus, non_mf_equity). Different modules,
    different arity — kept parallel in name to signal the shared rule.
    """
    return 2 if investable_corpus >= float(SUBGROUP_FUND_COUNT_THRESHOLD_INR) else 1


def _reconcile_rounding_dust(
    buys: list[FundBuy],
    targets: list[SubgroupTarget],
    deploy_amount: float,
    rounding_multiple: int,
) -> list[FundBuy]:
    """Push the residual left by nearest-₹100 per-fund rounding into the largest
    buy so a lumpsum deploys to the exact requested amount.

    Bounded to at most one rounding step per subgroup target: a shortfall larger
    than that is a genuine fund-scarcity gap and must stay in ``undeployed_inr``
    (the caller relies on that signal), not be masked here.
    """
    if not buys:
        return buys
    residual = deploy_amount - sum(b.amount_inr for b in buys)
    if 0 < residual < rounding_multiple * max(len(targets), 1):
        top = max(range(len(buys)), key=lambda i: buys[i].amount_inr)
        buys = list(buys)
        buys[top] = buys[top].model_copy(
            update={"amount_inr": buys[top].amount_inr + residual}
        )
    return buys


def run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput:
    """Deploy fresh money into specific funds: split by subgroup, select BUYs, frame SIP cadence.

    Returns the BUY list plus deployed/undeployed accounting; `undeployed_inr` is
    non-zero when fund scarcity (a subgroup with too few ranked funds, or a share
    rounding below one multiple), or no eligible long-term row for the post-goal
    remainder, prevents fully deploying the requested amount.
    """
    goal_share = inp.goal_share_inr
    if inp.cadence is Cadence.SIP_MONTHLY and inp.rounding_multiple_inr > 0:
        # The allocation engines hand over an exact rupee share; round it UP,
        # never to nearest, or the monthly SIP under-funds the goal.
        step = inp.rounding_multiple_inr
        goal_share = min(ceil(goal_share / step) * step, inp.deploy_amount_inr)
    bucket, targets = compute_goal_first_targets(
        inp.subgroups,
        inp.deploy_amount_inr,
        goal_share,
        inp.goal_subgroup,
        inp.exclude_subgroups,
        inp.current_value_by_subgroup if inp.cadence is Cadence.LUMPSUM else None,
    )
    # Each subgroup's target goes to its top-N ranked funds, N by corpus
    # (spec 2026-09-24); SIP and lumpsum now select identically.
    n_funds = _funds_per_subgroup(inp.investable_corpus_inr)
    buys = select_funds(targets, inp.ranked_funds, n_funds, inp.rounding_multiple_inr)
    if inp.cadence is Cadence.SIP_MONTHLY:
        # deploy_amount_inr is the MONTHLY amount; per-fund amounts are monthly.
        buys = [b.model_copy(update={"monthly_amount_inr": b.amount_inr}) for b in buys]
    else:
        # Reconcile nearest-₹100 rounding dust so the lumpsum deploys to the exact
        # requested amount; genuine fund-scarcity shortfalls stay in undeployed_inr.
        buys = _reconcile_rounding_dust(
            buys, targets, inp.deploy_amount_inr, inp.rounding_multiple_inr
        )
    deployed = sum(b.amount_inr for b in buys)
    return AdditionalInvestmentOutput(
        target_bucket=bucket,
        cadence=inp.cadence,
        deploy_amount_inr=inp.deploy_amount_inr,
        deployed_inr=deployed,
        # Nearest-₹100 rounding can edge the total a little over the deploy amount;
        # clamp so undeployed never goes negative (the output model requires ge=0).
        undeployed_inr=max(0.0, inp.deploy_amount_inr - deployed),
        per_subgroup_target=targets,
        buys=buys,
    )
