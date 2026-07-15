"""BUY-only fund selection from the ranking. Pure, no state, no I/O.

Two selectors: ``select_funds`` (lumpsum: per-fund caps + rank-spill) and
``select_funds_sip`` (SIP: mirror the rebalancing plan's BUY funds with a
rank-1 fallback, no caps). Holding-agnostic: every recommendation comes from
the ranked-fund list; the customer's existing holdings are not consulted.
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
    cap_floor_inr: float = 0.0,
) -> list[FundBuy]:
    """Allocate each subgroup target across its ranked funds (rank-1 first), capping per fund and rounding each buy to the nearest ₹100.

    The per-fund cap is ``max(cap_pct × deploy_amount, cap_floor_inr)``
    (amendment 2026-07-06): the rupee floor keeps a small lumpsum concentrated
    in few funds instead of fragmenting down the ranking; 0 (the default)
    disables the floor, preserving the original percentage-only behaviour.
    """
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    buys: list[FundBuy] = []
    for t in targets:
        cap_amt = max(
            _cap_amount(t.subgroup, deploy_amount, cap_pct_by_subgroup, default_cap_pct),
            cap_floor_inr,
        )
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


def select_funds_sip(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    rebal_buy_isins_by_subgroup: dict[str, list[str]] | None,
    deploy_amount: float,
    cap_pct_by_subgroup: dict[str, float],
    default_cap_pct: float,
    cap_floor_inr: float,
    rounding_multiple: int,
) -> list[FundBuy]:
    """SIP selection (spec 2026-07-05; per-fund cap amendment 2026-07-06).

    Per subgroup target, in two stages:

    1. MIRROR — the caller-supplied rebalancing BUY ISINs (latest persisted
       run, ordered by BUY amount desc) that exist in THIS subgroup's own
       ranked list get an equal split of the target, each clamped at the
       per-fund cap.
    2. WALK — any remainder (cap overflow, or the whole target when there are
       no candidates) walks down the subgroup's ranking, each fund up to the
       cap, skipping funds already bought. Ranking exhaustion leaves the rest
       for ``undeployed_inr``.

    The per-fund cap is ``max(cap_pct × deploy_amount, cap_floor_inr)`` — the
    rupee floor keeps a small monthly amount concentrated in few funds; the
    percentage keeps a large one from over-concentrating. The equal-split
    stage rounds each share independently (nearest ``rounding_multiple``), so
    a small overshoot vs the target is possible — an accepted trade-off.
    """
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    rebal = rebal_buy_isins_by_subgroup or {}
    buys: list[FundBuy] = []
    for t in targets:
        sg_funds = ranked_by_sg.get(t.subgroup, [])
        if not sg_funds:
            # No ranked funds for this subgroup: no buy — the amount surfaces in
            # undeployed_inr. Defensive: unreachable in production (every
            # producible subgroup carries ranked funds), but must not crash.
            continue
        cap_amt = max(
            _cap_amount(t.subgroup, deploy_amount, cap_pct_by_subgroup, default_cap_pct),
            cap_floor_inr,
        )
        by_isin = {f.isin: f for f in sg_funds}
        candidates: list[RankedFund] = []
        for isin in rebal.get(t.subgroup, []):
            fund = by_isin.get(isin)  # per-subgroup match: stale/wrong-subgroup ISINs drop out
            if fund is not None and fund not in candidates:
                candidates.append(fund)

        remaining = t.target_inr
        bought_isins: set[str] = set()

        # Stage 1 — mirror the rebalancing plan: equal split, clamped at cap.
        if candidates:
            share = _round_to_multiple(
                min(remaining / len(candidates), cap_amt), rounding_multiple
            )
            if share < rounding_multiple:
                # Dust consolidation: equal shares would round to zero — try
                # the whole (capped, rounded) target on the first candidate.
                candidates = candidates[:1]
                share = _round_to_multiple(min(remaining, cap_amt), rounding_multiple)
            if share >= rounding_multiple:
                for fund in candidates:
                    buys.append(FundBuy(
                        recommended_fund=fund.recommended_fund,
                        isin=fund.isin,
                        sub_category=fund.sub_category,
                        asset_subgroup=t.subgroup,
                        amount_inr=share,
                        reason="Matches your rebalancing plan",
                    ))
                    bought_isins.add(fund.isin)
                    remaining -= share

        # Stage 2 — walk the ranking with the remainder (the whole target when
        # there were no candidates), each fund up to the cap.
        for fund in sg_funds:
            if remaining < rounding_multiple:
                break
            if fund.isin in bought_isins:
                continue
            buy_amt = _round_to_multiple(min(remaining, cap_amt), rounding_multiple)
            if buy_amt < rounding_multiple:
                continue
            buys.append(FundBuy(
                recommended_fund=fund.recommended_fund,
                isin=fund.isin,
                sub_category=fund.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=buy_amt,
                reason="Top-ranked fund for this category",
            ))
            remaining -= buy_amt
    return buys
