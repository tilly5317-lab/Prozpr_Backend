"""BUY-only fund selection from the ranking. Pure, no state, no I/O.

Each subgroup's target is split equally across its top-N ranked funds (rank 1
first), N decided upstream from the corpus (spec 2026-09-24). The target is
rounded once, then split in whole multiples, so a subgroup's total is the same
with one fund or two. Holding-agnostic: every recommendation comes from the
ranked-fund list.
"""

from __future__ import annotations

from .models import FundBuy, RankedFund, SubgroupTarget


def _round_to_multiple(amount: float, multiple: int) -> float:
    """Round an amount to the NEAREST `multiple` (e.g. ₹100), halves rounding up.

    Nearest (not floor) so the deployed total lands close to the target in both
    directions. It can overshoot by up to half a multiple per subgroup; nothing
    trims the buys — the pipeline only floors ``undeployed_inr`` at zero.
    """
    if multiple <= 0:
        return amount
    return float(int(amount / multiple + 0.5) * multiple)


def _split_in_steps(total: float, n: int, multiple: int) -> list[float]:
    """Split a rounded total across ``n`` funds in whole multiples, as equally as
    possible; the higher-ranked funds take any extra step."""
    if multiple <= 0:
        return [total / n] * n
    base, extra = divmod(int(round(total / multiple)), n)
    return [float((base + (1 if i < extra else 0)) * multiple) for i in range(n)]


def select_funds(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    n_funds: int,
    rounding_multiple: int,
) -> list[FundBuy]:
    """Split each subgroup target equally across its top-`n_funds` ranked funds.

    The target is rounded to the nearest multiple first, then split in whole
    multiples (rank 1 takes any extra step); a fund left below one multiple gets
    no buy. A target rounding to zero surfaces in ``undeployed_inr`` / is topped
    up by the lumpsum dust reconciliation. Fewer than `n_funds` ranked funds in a
    subgroup → however many exist.
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
        total = _round_to_multiple(t.target_inr, rounding_multiple)
        shares = _split_in_steps(total, len(recipients), rounding_multiple)
        for f, share in zip(recipients, shares):
            if share < rounding_multiple:
                continue
            buys.append(FundBuy(
                recommended_fund=f.recommended_fund,
                isin=f.isin,
                sub_category=f.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=share,
                reason="Recommended fund for this category",
            ))
    return buys
