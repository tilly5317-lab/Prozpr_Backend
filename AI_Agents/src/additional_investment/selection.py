"""BUY-only fund selection from the ranking. Pure, no state, no I/O.

Each subgroup's target is split equally across its top-N ranked funds (rank 1
first), N decided upstream from the corpus (spec 2026-09-24). Holding-agnostic:
every recommendation comes from the ranked-fund list.
"""

from __future__ import annotations

from .models import FundBuy, RankedFund, SubgroupTarget


def _round_to_multiple(amount: float, multiple: int) -> float:
    """Round an amount to the NEAREST `multiple` (e.g. ₹100), halves rounding up.

    Nearest (not floor) so the deployed total lands close to the target in both
    directions; the small per-fund overshoot this can introduce is clamped at the
    deploy total by the pipeline's lumpsum dust reconciliation.
    """
    if multiple <= 0:
        return amount
    return float(int(amount / multiple + 0.5) * multiple)


def select_funds(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    n_funds: int,
    rounding_multiple: int,
) -> list[FundBuy]:
    """Split each subgroup target equally across its top-`n_funds` ranked funds.

    Each recipient gets ``round_to_multiple(target / n, rounding_multiple)``; a
    share below one multiple is skipped (its money surfaces in ``undeployed_inr``
    / is topped up by the lumpsum dust reconciliation). Fewer than `n_funds`
    ranked funds in a subgroup → however many exist.
    """
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    buys: list[FundBuy] = []
    for t in targets:
        recipients = ranked_by_sg.get(t.subgroup, [])[: max(1, n_funds)]
        if not recipients:
            continue
        share = _round_to_multiple(t.target_inr / len(recipients), rounding_multiple)
        if share < rounding_multiple:
            continue
        for f in recipients:
            buys.append(FundBuy(
                recommended_fund=f.recommended_fund,
                isin=f.isin,
                sub_category=f.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=share,
                reason="Recommended fund for this category",
            ))
    return buys
