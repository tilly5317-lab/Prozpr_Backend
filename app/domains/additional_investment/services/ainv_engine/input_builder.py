"""Materialise an AdditionalInvestmentInput from the practical allocation.

Money is plain ``float`` (allocation family, not Decimal). The builder reads no
DB, ledger or NAV: subgroup rows come from the practical allocation, the goal
share is computed by the caller with ``goal_share_for``, and the BUY list comes
from the ranked-fund CSV. A lumpsum's ``current_value_by_subgroup`` is
pre-aggregated by the service. The two synthetic rows (ELSS + non-MF equity) are
passed through and excluded via ``exclude_subgroups``, not hand-dropped.
"""

from __future__ import annotations

from typing import Any

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentInput,
    Cadence,
    RankedFund,
    SubgroupBucketAmounts,
)
from Rebalancing.config import (  # type: ignore[import-not-found]  # noqa: E402
    AINV_LUMPSUM_FUND_CAP_FLOOR_INR,
    AINV_SIP_FUND_CAP_FLOOR_INR,
    OTHERS_FUND_CAP_PCT,
)
from Rebalancing.tables import cap_pct_for  # type: ignore[import-not-found]  # noqa: E402


# Synthetic practical-allocation rows the additional-investment engine never
# buys into: ELSS (SEBI 3-yr lock-in) and non-MF equity (direct stocks / PMS).
# This engine is MF-BUY-only, so they are handed to the engine as
# ``exclude_subgroups`` — NOT hand-dropped from the subgroup list. The engine
# zero-weights them and renormalises the split onto the remaining (eligible)
# subgroups.
_EXCLUDE_SUBGROUPS = frozenset({"tax_efficient_equities", "non_mf_equities"})


def goal_share_for(
    allocation_output: Any, cadence: Cadence, deploy_amount_inr: float
) -> tuple[float, str | None]:
    """Money for short-term goals out of this deployment, and the subgroup it buys."""
    funding = getattr(allocation_output, "goal_funding", None)
    if funding is None:
        return 0.0, None
    share = (
        funding.monthly_sip_to_goals
        if cadence is Cadence.SIP_MONTHLY
        else funding.from_corpus
    )
    return min(float(share), deploy_amount_inr), funding.asset_subgroup


async def build_additional_investment_input_for_user(
    allocation_output: Any,
    *,
    deploy_amount_inr: float,
    cadence: Cadence,
    current_value_by_subgroup: dict[str, float] | None = None,
    investable_corpus_inr: float = 0.0,
    goal_share_inr: float = 0.0,
    goal_subgroup: str | None = None,
) -> tuple[AdditionalInvestmentInput, dict[str, Any]]:
    """Return ``(input, debug_dict)`` for ``run_additional_investment(...)``."""
    # 1. Per-subgroup bucket amounts from the practical allocation — ALL rows pass
    #    through verbatim. The synthetic rows are dropped by the engine via
    #    exclude_subgroups (below), NOT hand-filtered here.
    subgroups = [
        SubgroupBucketAmounts(**row.model_dump())
        for row in allocation_output.aggregated_subgroups
    ]

    deficit_mode = (
        cadence is Cadence.LUMPSUM and current_value_by_subgroup is not None
    )

    # 2. Ranked funds: flatten the per-subgroup ranking, carrying scheme_code (T2).
    ranking = get_fund_ranking()
    ranked_funds = [
        RankedFund(
            asset_subgroup=rr.asset_subgroup,
            sub_category=rr.sub_category,
            rank=rr.rank,
            isin=rr.isin,
            scheme_code=rr.scheme_code,
            recommended_fund=rr.fund_name,
        )
        for rows in ranking.values()
        for rr in rows
    ]

    # 3. Per-subgroup caps over the ELIGIBLE rows (OTHERS default for unmapped
    #    subgroups). The cap is a percent of the DEPLOY amount, applied inside the
    #    engine — the builder reads no corpus total.
    cap_pct_by_subgroup = {
        s.subgroup: cap_pct_for(s.subgroup)
        for s in subgroups
        if s.subgroup not in _EXCLUDE_SUBGROUPS
    }

    inp = AdditionalInvestmentInput(
        deploy_amount_inr=deploy_amount_inr,
        cadence=cadence,
        subgroups=subgroups,
        goal_share_inr=goal_share_inr,
        goal_subgroup=goal_subgroup,
        ranked_funds=ranked_funds,
        cap_pct_by_subgroup=cap_pct_by_subgroup,
        default_cap_pct=OTHERS_FUND_CAP_PCT,
        exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
        current_value_by_subgroup=(
            current_value_by_subgroup if deficit_mode else None
        ),
        # 1 vs 2 funds per subgroup by corpus (spec 2026-09-24); pre-computed by
        # the caller as total_corpus − non_mf_equity, cadence-aware.
        investable_corpus_inr=investable_corpus_inr,
        # Vestigial cap knobs — retained on the model, ignored by selection since
        # spec 2026-09-24 (kept like medium_term/max_pct to avoid builder churn).
        sip_fund_cap_floor_inr=AINV_SIP_FUND_CAP_FLOOR_INR,
        lumpsum_fund_cap_floor_inr=AINV_LUMPSUM_FUND_CAP_FLOOR_INR,
    )
    debug = {
        "deployment_mode": "deficit_fill" if deficit_mode else "long_term",
        "subgroup_count": len(subgroups),
        "ranked_fund_count": len(ranked_funds),
        "goal_share_inr": goal_share_inr,
        "goal_subgroup": goal_subgroup,
        "exclude_subgroups": sorted(_EXCLUDE_SUBGROUPS),
    }
    return inp, debug
