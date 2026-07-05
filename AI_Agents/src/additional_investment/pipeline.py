"""Pipeline orchestrator for additional-investment deployment. Pure-sync, DB-free."""

from __future__ import annotations

from .models import (
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
    Cadence,
)
from .ratio import compute_deficit_targets, compute_targets, dominant_bucket
from .selection import select_funds


def run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput:
    """Deploy fresh money into specific funds: split by subgroup, select BUYs, frame SIP cadence.

    Returns the BUY list plus deployed/undeployed accounting; `undeployed_inr` is
    non-zero when caps or fund scarcity prevent fully deploying the requested amount.
    """
    if inp.cadence is Cadence.LUMPSUM and inp.current_value_by_subgroup is not None:
        # Deficit fill (spec 2026-07-03): deploy into the gaps between the
        # post-investment ideal (caller ran PAA at corpus + deploy) and current
        # holdings. target_bucket becomes the dominant horizon of the deployed
        # money — a truthful label, not the split driver.
        targets = compute_deficit_targets(
            inp.subgroups, inp.current_value_by_subgroup,
            inp.deploy_amount_inr, inp.exclude_subgroups,
        )
        bucket = dominant_bucket(targets, inp.subgroups)
    else:
        bucket, targets = compute_targets(
            inp.subgroups, inp.short_term_fulfilled, inp.medium_term_fulfilled,
            inp.deploy_amount_inr, inp.exclude_subgroups,
        )
    buys = select_funds(
        targets,
        inp.ranked_funds,
        inp.deploy_amount_inr,
        inp.cap_pct_by_subgroup,
        inp.default_cap_pct,
        inp.rounding_multiple_inr,
    )
    if inp.cadence is Cadence.SIP_MONTHLY:
        # deploy_amount_inr is the MONTHLY amount; per-fund amounts are monthly.
        buys = [b.model_copy(update={"monthly_amount_inr": b.amount_inr}) for b in buys]
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
