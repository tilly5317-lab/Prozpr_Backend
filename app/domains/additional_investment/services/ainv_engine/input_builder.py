"""Materialise an AdditionalInvestmentInput from TurnContext + allocation output.

Mirrors rebal_engine/input_builder.py, but for the BUY-only additional-investment
engine: money is plain ``float`` (allocation family, not Decimal). There is no
holdings path on the LEGACY (SIP / single-bucket) path; the lumpsum deficit path
receives a pre-aggregated ``current_value_by_subgroup`` map from the service
(see ``holdings_snapshot.py``) — the builder itself still reads no DB ledger and
no NAV. The engine recommends purely from the ranked-fund list, and the per-fund
caps key off the DEPLOY amount, so the builder reads no existing-corpus total
and computes no resulting-corpus figure.
ALL practical-allocation subgroup rows are passed through verbatim; the two
synthetic rows (ELSS + non-MF equity) are NOT hand-dropped here — the builder
sets ``exclude_subgroups`` and the engine gives them zero weight and renormalises
the split onto the remaining (eligible) subgroups.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.cashflow.services.cashflow_compute_service import (
    run_cashflow_projection_for_user,
)
from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentInput,
    Cadence,
    RankedFund,
    SubgroupBucketAmounts,
)
from asset_allocation_pydantic.tables import (  # type: ignore[import-not-found]  # noqa: E402
    LONG_TERM_BOUNDARY_MONTHS,
    MEDIUM_TERM_BOUNDARY_MONTHS,
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


def _months_to(asof: date, goal_date: date) -> int:
    """Whole calendar months from ``asof`` to ``goal_date`` (day-of-month ignored)."""
    return (goal_date.year - asof.year) * 12 + (goal_date.month - asof.month)


async def _goal_funding_flags(user, asof: date) -> tuple[bool, bool]:
    """Return ``(short_term_fulfilled, medium_term_fulfilled)``.

    short_term_fulfilled is True when every goal under MEDIUM_TERM_BOUNDARY_MONTHS
    (24) is funded — or there are none. medium_term_fulfilled is True when every
    goal in [24, LONG_TERM_BOUNDARY_MONTHS=60) is funded — or there are none. The
    engine targets the nearest unfunded bucket (short → medium → long), so
    long-term needs no flag (it is always the fallback target).
    """
    snapshot = await run_cashflow_projection_for_user(user, anchor_date=asof)
    short_goals = [
        g
        for g in snapshot.goals
        if _months_to(asof, g.goal_date) < MEDIUM_TERM_BOUNDARY_MONTHS
    ]
    medium_goals = [
        g
        for g in snapshot.goals
        if MEDIUM_TERM_BOUNDARY_MONTHS
        <= _months_to(asof, g.goal_date)
        < LONG_TERM_BOUNDARY_MONTHS
    ]
    short_term_fulfilled = all(g.is_funded for g in short_goals)
    medium_term_fulfilled = all(g.is_funded for g in medium_goals)
    return short_term_fulfilled, medium_term_fulfilled


async def build_additional_investment_input_for_user(
    ctx: "TurnContext",
    allocation_output: Any,
    *,
    deploy_amount_inr: float,
    cadence: Cadence,
    current_value_by_subgroup: dict[str, float] | None = None,
    rebal_buy_isins_by_subgroup: dict[str, list[str]] | None = None,
) -> tuple[AdditionalInvestmentInput, dict[str, Any]]:
    """Return ``(input, debug_dict)`` for ``run_additional_investment(...)``.

    Holding-agnostic: the only DB-backed collaborator is the cashflow projection
    (for the short/medium-term goal-funding flags); the BUY list is derived purely
    from the ranked-fund CSV, and the per-fund caps key off the deploy amount, so
    no corpus total is read.
    """
    user = ctx.user_ctx
    asof = date.today()

    # 1. Per-subgroup bucket amounts from the practical allocation — ALL rows pass
    #    through verbatim. The synthetic rows are dropped by the engine via
    #    exclude_subgroups (below), NOT hand-filtered here.
    subgroups = [
        SubgroupBucketAmounts(**row.model_dump())
        for row in allocation_output.aggregated_subgroups
    ]

    # 2. Goal-funding flags — LEGACY path only (SIP, or lumpsum without a
    #    holdings map). The deficit path skips the cashflow projection entirely:
    #    its only consumer here was the nearest-unfunded label, which deficit
    #    mode derives from the deployed money instead (spec 2026-07-03).
    deficit_mode = (
        cadence is Cadence.LUMPSUM and current_value_by_subgroup is not None
    )
    if deficit_mode:
        short_term_fulfilled, medium_term_fulfilled = False, False
    else:
        short_term_fulfilled, medium_term_fulfilled = await _goal_funding_flags(
            user, asof
        )

    # 3. Ranked funds: flatten the per-subgroup ranking, carrying scheme_code (T2).
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

    # 4. Per-subgroup caps over the ELIGIBLE rows (OTHERS default for unmapped
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
        short_term_fulfilled=short_term_fulfilled,
        medium_term_fulfilled=medium_term_fulfilled,
        ranked_funds=ranked_funds,
        cap_pct_by_subgroup=cap_pct_by_subgroup,
        default_cap_pct=OTHERS_FUND_CAP_PCT,
        exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
        current_value_by_subgroup=(
            current_value_by_subgroup if deficit_mode else None
        ),
        # SIP-only: latest rebalancing run's BUY ISINs per subgroup (None on
        # lumpsum and when the read found nothing — engine falls back to rank-1).
        rebal_buy_isins_by_subgroup=rebal_buy_isins_by_subgroup,
        # Per-fund cap floors — cap is max(pct × deploy, floor) per cadence.
        sip_fund_cap_floor_inr=AINV_SIP_FUND_CAP_FLOOR_INR,
        lumpsum_fund_cap_floor_inr=AINV_LUMPSUM_FUND_CAP_FLOOR_INR,
    )
    debug = {
        "deployment_mode": "deficit_fill" if deficit_mode else "single_bucket",
        "subgroup_count": len(subgroups),
        "ranked_fund_count": len(ranked_funds),
        "short_term_fulfilled": short_term_fulfilled,
        "medium_term_fulfilled": medium_term_fulfilled,
        "exclude_subgroups": sorted(_EXCLUDE_SUBGROUPS),
    }
    return inp, debug
