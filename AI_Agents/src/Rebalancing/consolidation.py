"""Deterministic buy-side reshape for constraint-aware consolidation (F3-B).

Pure: operates only on a RebalancingComputeResponse. The engine runs ONCE,
unmodified; this redistributes ONLY the buy budget across the funds the
customer allows, preserving the total buy and every sell. No I/O, no CSV.

Distribution rule (displaced-budget pro-rata): survivors keep their
engine-given buy amounts frozen; only the dropped funds' budget moves, spread
pro-rata to the survivors' amounts. Identity when nothing is dropped.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]

_ONE = Decimal("1")


@dataclass(frozen=True)
class ConsolidationConstraints:
    target_fund_count: int | None = None                 # max NEW-BUY funds
    allowed_categories: tuple[str, ...] | None = None    # redeploy whole budget here
    # NO reset flag: stateless design — "back to the full plan" is narrate mode.


def constraints_active(c: ConsolidationConstraints) -> bool:
    return c.target_fund_count is not None or bool(c.allowed_categories)


@dataclass(frozen=True)
class BuyCandidate:
    isin: str
    recommended_fund: str
    sub_category: str | None
    asset_subgroup: str
    rank: int
    buy_inr: Decimal


def _round_to_multiple(x: Decimal, multiple: int) -> Decimal:
    if multiple <= 0:
        return x
    m = Decimal(multiple)
    return (x / m).quantize(_ONE, rounding=ROUND_HALF_UP) * m


def compute_reshaped_buys(
    candidates: Iterable[BuyCandidate],
    constraints: ConsolidationConstraints,
    *,
    rounding_multiple: int = 100,
) -> dict[str, Decimal]:
    """Displaced-budget pro-rata reshape.

    Survivors keep their engine-given buy amounts FROZEN; only the dropped
    funds' combined budget moves, spread pro-rata to the survivors' amounts.
    Identity when nothing is dropped. Total preserved exactly; rounding
    residual onto the largest surviving buy. Returns isin -> new buy (Decimal).
    """
    cands = list(candidates)
    total = sum((c.buy_inr for c in cands), Decimal(0))
    if total <= 0 or not cands:
        return {c.isin: Decimal(0) for c in cands}

    # 1. Survivors: filter to allowed categories (match on sub_category — the
    #    SEBI-name vocabulary resolve_category emits; asset_subgroup is too
    #    coarse, e.g. multi_asset holds ten sub_categories), then keep top-N
    #    (rank asc, larger buy first).
    if constraints.allowed_categories:
        allowed = set(constraints.allowed_categories)
        eligible = [c for c in cands if c.sub_category in allowed]
        if not eligible:                        # honest no-op; caller surfaces error
            return {c.isin: Decimal(0) for c in cands}
    else:
        eligible = cands
    ordered = sorted(eligible, key=lambda c: (c.rank, -c.buy_inr))
    keep = (
        ordered[: max(1, constraints.target_fund_count)]
        if constraints.target_fund_count is not None
        else ordered
    )

    # 2. Identity fast-path: nothing dropped → nothing moves.
    if len(keep) == len(cands):
        return {c.isin: c.buy_inr for c in cands}

    # 3. Displaced budget = everything not surviving; spread pro-rata to the
    #    survivors' own (frozen) amounts. Caps deliberately not re-imposed.
    kept_total = sum((c.buy_inr for c in keep), Decimal(0))
    displaced = total - kept_total

    out: dict[str, Decimal] = {c.isin: Decimal(0) for c in cands}
    for c in keep:
        share = (
            displaced * c.buy_inr / kept_total
            if kept_total > 0
            else displaced / Decimal(len(keep))
        )
        out[c.isin] = c.buy_inr + _round_to_multiple(share, rounding_multiple)

    # 4. Preserve total exactly: residual onto the largest surviving buy.
    placed = sum(out.values(), Decimal(0))
    residual = total - placed
    if residual != 0:
        biggest = max(keep, key=lambda c: out[c.isin])
        out[biggest.isin] += residual
    return out


def _buy_key(obj) -> str:
    return getattr(obj, "isin", None) or getattr(obj, "recommended_fund", None) or ""


def reshape_response(
    response: RebalancingComputeResponse,
    constraints: ConsolidationConstraints,
    *,
    rounding_multiple: int = 100,
) -> tuple[RebalancingComputeResponse, str | None]:
    """Return a deep-copied response with buys reshaped per the constraints.

    Rewrites EVERY buy representation together — rows, subgroups[].actions,
    per-subgroup total_buy_inr, trade_list BUY entries, and funds_to_buy_count
    — so no downstream reader (e.g. the fallback brief) narrates stale buys.
    Sells and tax are untouched. Second element is an error code
    ("category_not_in_plan") or None. Returns the SAME object (no copy) when
    no constraint is active.
    """
    if not constraints_active(constraints):
        return response, None

    candidates = [
        BuyCandidate(
            isin=_buy_key(r), recommended_fund=r.recommended_fund or "",
            sub_category=r.sub_category, asset_subgroup=r.asset_subgroup,
            rank=int(getattr(r, "rank", 0) or 0),
            buy_inr=Decimal(getattr(r, "pass1_buy_amount", 0) or 0),
        )
        for r in response.rows
        if Decimal(getattr(r, "pass1_buy_amount", 0) or 0) > 0
    ]
    if constraints.allowed_categories:
        present = {c.sub_category for c in candidates}
        if not (present & set(constraints.allowed_categories)):
            return response, "category_not_in_plan"

    new_buys = compute_reshaped_buys(
        candidates, constraints, rounding_multiple=rounding_multiple)

    out = copy.deepcopy(response)
    for r in out.rows:
        k = _buy_key(r)
        if k in new_buys:
            r.pass1_buy_amount = new_buys[k]
    for sg in out.subgroups:
        sg_buy = Decimal(0)
        for a in sg.actions:
            k = _buy_key(a)
            if k in new_buys:
                a.pass1_buy_amount = new_buys[k]
            sg_buy += Decimal(getattr(a, "pass1_buy_amount", 0) or 0)
        sg.total_buy_inr = sg_buy

    # trade_list carries the same buys a third time — rewrite/drop, sells untouched.
    new_trades = []
    for t in out.trade_list:
        if t.action == "BUY":
            k = _buy_key(t)
            if k in new_buys:
                if new_buys[k] <= 0:
                    continue
                t.amount_inr = new_buys[k]
        new_trades.append(t)
    out.trade_list = new_trades

    out.totals.funds_to_buy_count = sum(1 for v in new_buys.values() if v > 0)
    return out, None
