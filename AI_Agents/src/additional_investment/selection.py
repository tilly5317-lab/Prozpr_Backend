"""BUY-only fund selection from the ranking under per-fund caps. Pure, no state, no I/O.

Holding-agnostic: every recommendation comes from the ranked-fund list; the
customer's existing holdings are not consulted.
"""

from __future__ import annotations

from .models import FundBuy, RankedFund, SubgroupTarget


def _round_to_multiple(amount: float, multiple: int) -> float:
    """Round an amount to the NEAREST `multiple` (e.g. ₹100), halves rounding up.

    Nearest (not floor) so the deployed total lands close to the deploy amount in
    both directions rather than always a little under. The small per-fund overshoot
    this can introduce is clamped at the deploy total in the pipeline.
    """
    if multiple <= 0:
        return amount
    return float(int(amount / multiple + 0.5) * multiple)


def _cap_amount(subgroup: str, deploy_amount: float,
                cap_pct_by_subgroup: dict[str, float], default_cap_pct: float) -> float:
    """Per-fund rupee cap for a subgroup: its cap percent of the DEPLOY amount.

    Keyed off the deposit (this SIP/lumpsum), not the corpus — so no single fund
    receives more than `pct%` of the money being deployed, forcing a subgroup's
    share to spread across its top-ranked funds.
    """
    pct = cap_pct_by_subgroup.get(subgroup, default_cap_pct)
    return deploy_amount * pct / 100.0


def select_funds(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    deploy_amount: float,
    cap_pct_by_subgroup: dict[str, float],
    default_cap_pct: float,
    rounding_multiple: int,
) -> list[FundBuy]:
    """Allocate each subgroup target across its ranked funds (rank-1 first), capping per fund and rounding each buy to the nearest ₹100."""
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    buys: list[FundBuy] = []
    for t in targets:
        cap_amt = _cap_amount(t.subgroup, deploy_amount, cap_pct_by_subgroup, default_cap_pct)
        remaining = t.target_inr
        for f in ranked_by_sg.get(t.subgroup, []):
            if remaining < rounding_multiple:
                break
            if cap_amt < rounding_multiple:
                continue
            buy_amt = _round_to_multiple(min(remaining, cap_amt), rounding_multiple)
            if buy_amt < rounding_multiple:
                continue
            buys.append(FundBuy(
                recommended_fund=f.recommended_fund,
                isin=f.isin,
                sub_category=f.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=buy_amt,
                reason="Recommended fund for this category",
            ))
            remaining -= buy_amt
    return buys
