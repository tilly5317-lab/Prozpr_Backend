from __future__ import annotations

from additional_investment.models import (
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
    Cadence,
)
from additional_investment.ratio import compute_targets
from additional_investment.selection import select_funds


def run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput:
    branch, targets = compute_targets(
        inp.subgroups, inp.medium_term_fulfilled, inp.deploy_amount_inr
    )
    buys = select_funds(
        targets,
        inp.ranked_funds,
        inp.holdings,
        inp.resulting_corpus_inr,
        inp.cap_pct_by_subgroup,
        inp.default_cap_pct,
        inp.rounding_multiple_inr,
    )
    if inp.cadence is Cadence.SIP_MONTHLY:
        # deploy_amount_inr is the MONTHLY amount; per-fund amounts are monthly.
        buys = [b.model_copy(update={"monthly_amount_inr": b.amount_inr}) for b in buys]
    deployed = sum(b.amount_inr for b in buys)
    return AdditionalInvestmentOutput(
        branch_used=branch,
        cadence=inp.cadence,
        deploy_amount_inr=inp.deploy_amount_inr,
        deployed_inr=deployed,
        undeployed_inr=inp.deploy_amount_inr - deployed,
        per_subgroup_target=targets,
        buys=buys,
    )
