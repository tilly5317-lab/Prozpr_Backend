"""Pipeline orchestrator for additional-investment deployment. Pure-sync, DB-free."""

from __future__ import annotations

from .models import (
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
    Cadence,
    FundBuy,
    SubgroupTarget,
)
from .ratio import compute_deficit_targets, compute_targets, dominant_bucket
from .selection import select_funds, select_funds_sip


def _reconcile_rounding_dust(
    buys: list[FundBuy],
    targets: list[SubgroupTarget],
    deploy_amount: float,
    rounding_multiple: int,
) -> list[FundBuy]:
    """Push the residual left by nearest-₹100 per-fund rounding into the largest
    buy so a lumpsum deploys to the exact requested amount.

    Bounded to at most one rounding step per subgroup target: a shortfall larger
    than that is a genuine cap / fund-scarcity gap and must stay in
    ``undeployed_inr`` (the caller relies on that signal), not be masked here.
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
    if inp.cadence is Cadence.SIP_MONTHLY:
        # SIP mirrors the latest rebalancing plan's BUY funds (spec 2026-07-05):
        # equal split per subgroup, rank-walk fallback/overflow, per-fund cap
        # of max(cap_pct × deploy, sip_fund_cap_floor_inr) — amendment 2026-07-06.
        buys = select_funds_sip(
            targets,
            inp.ranked_funds,
            inp.rebal_buy_isins_by_subgroup,
            inp.deploy_amount_inr,
            inp.cap_pct_by_subgroup,
            inp.default_cap_pct,
            inp.sip_fund_cap_floor_inr,
            inp.rounding_multiple_inr,
        )
        # deploy_amount_inr is the MONTHLY amount; per-fund amounts are monthly.
        buys = [b.model_copy(update={"monthly_amount_inr": b.amount_inr}) for b in buys]
    else:
        buys = select_funds(
            targets,
            inp.ranked_funds,
            inp.deploy_amount_inr,
            inp.cap_pct_by_subgroup,
            inp.default_cap_pct,
            inp.rounding_multiple_inr,
            cap_floor_inr=inp.lumpsum_fund_cap_floor_inr,
        )
        # Reconcile nearest-₹100 rounding dust so the lumpsum deploys to the exact
        # requested amount; genuine cap/scarcity shortfalls stay in undeployed_inr.
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
