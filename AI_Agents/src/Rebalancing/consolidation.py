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
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]

_ONE = Decimal("1")


@dataclass(frozen=True)
class ConsolidationConstraints:
    target_fund_count: int | None = None                 # max NEW-BUY funds
    allowed_categories: tuple[str, ...] | None = None    # redeploy whole budget here
    excluded_categories: tuple[str, ...] | None = None   # never buy these
    category_weight_targets: dict[str, float] | None = None  # sub_category -> share of total buy (0-1)
    # NO include_fund: named-fund inclusion is Phase 2, at the input-builder
    # seam where the engine computes real numbers for the injected fund.
    # NO reset flag: stateless design — "back to the full plan" is narrate mode.


def constraints_active(c: ConsolidationConstraints) -> bool:
    return (
        c.target_fund_count is not None
        or bool(c.allowed_categories)
        or bool(c.excluded_categories)
        or bool(c.category_weight_targets)
    )


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

    Legacy-only constraints (allowed_categories / bare target_fund_count) run
    the legacy path, split two ways: allowed_categories keeps the original
    F3-B portfolio-wide "redeploy the whole budget" behaviour byte-for-byte;
    a bare target_fund_count is subgroup-aware — it preserves each
    asset_subgroup's own buy total and only redistributes WITHIN a subgroup,
    so a fund-count trim can never undo a market-cap tilt. Exclusions and
    weight targets take the extended path, which reuses the same arithmetic
    form and is (for now) portfolio-wide/tilt-unaware like the old count path.
    """
    cands = list(candidates)
    total = sum((c.buy_inr for c in cands), Decimal(0))
    if total <= 0 or not cands:
        return {c.isin: Decimal(0) for c in cands}
    if not constraints.excluded_categories and not constraints.category_weight_targets:
        return _reshape_legacy(cands, constraints, total,
                               rounding_multiple=rounding_multiple)
    return _reshape_extended(cands, constraints, total,
                             rounding_multiple=rounding_multiple)


def _reshape_legacy(
    cands: list[BuyCandidate],
    constraints: ConsolidationConstraints,
    total: Decimal,
    *,
    rounding_multiple: int,
) -> dict[str, Decimal]:
    # allowed_categories keeps its portfolio-wide "redeploy the whole budget into
    # these categories" semantics — UNCHANGED (cross-subgroup movement is the point).
    # Match on sub_category — the SEBI-name vocabulary resolve_category emits;
    # asset_subgroup is too coarse, e.g. multi_asset holds ten sub_categories.
    if constraints.allowed_categories:
        allowed = set(constraints.allowed_categories)
        eligible = [c for c in cands if c.sub_category in allowed]
        if not eligible:                        # honest no-op; caller surfaces error
            return {c.isin: Decimal(0) for c in cands}
        ordered = sorted(eligible, key=lambda c: (c.rank, -c.buy_inr))
        keep = (
            ordered[: max(1, constraints.target_fund_count)]
            if constraints.target_fund_count is not None
            else ordered
        )
        if len(keep) == len(cands):
            return {c.isin: c.buy_inr for c in cands}
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
        placed = sum(out.values(), Decimal(0))
        residual = total - placed
        if residual != 0:
            biggest = max(keep, key=lambda c: out[c.isin])
            out[biggest.isin] += residual
        return out

    # Bare target_fund_count: SUBGROUP-AWARE. Preserve each subgroup's buy total so a
    # count trim never pulls money out of a market-cap tilt. Redistribute WITHIN a
    # subgroup only; count floor = number of subgroups with buys.
    out: dict[str, Decimal] = {c.isin: Decimal(0) for c in cands}
    by_sg: dict[str, list[BuyCandidate]] = defaultdict(list)
    for c in cands:
        by_sg[c.asset_subgroup].append(c)
    keep_per_sg: dict[str, int] = {sg: 1 for sg in by_sg}
    if constraints.target_fund_count is not None:
        extra = max(0, constraints.target_fund_count - len(by_sg))
        sgs = list(by_sg)  # plain round-robin; spec fixes no extra-slot ordering
        i = 0
        while extra > 0 and any(keep_per_sg[s] < len(by_sg[s]) for s in sgs):
            sg = sgs[i % len(sgs)]
            if keep_per_sg[sg] < len(by_sg[sg]):
                keep_per_sg[sg] += 1
                extra -= 1
            i += 1
    else:
        keep_per_sg = {sg: len(by_sg[sg]) for sg in by_sg}
    for sg, group in by_sg.items():
        sg_total = sum((c.buy_inr for c in group), Decimal(0))
        ordered = sorted(group, key=lambda c: (c.rank, -c.buy_inr))
        keep = ordered[: keep_per_sg[sg]]
        kept_total = sum((c.buy_inr for c in keep), Decimal(0))
        displaced = sg_total - kept_total
        for c in keep:
            share = (
                displaced * c.buy_inr / kept_total
                if kept_total > 0
                else displaced / Decimal(len(keep))
            )
            out[c.isin] = c.buy_inr + _round_to_multiple(share, rounding_multiple)
        placed = sum(out[c.isin] for c in keep)
        residual = sg_total - placed
        if residual != 0 and keep:
            biggest = max(keep, key=lambda c: out[c.isin])
            out[biggest.isin] += residual
    return out


def _reshape_extended(
    cands: list[BuyCandidate],
    constraints: ConsolidationConstraints,
    total: Decimal,
    *,
    rounding_multiple: int,
) -> dict[str, Decimal]:
    """Filters → weight targets → count trim, same arithmetic form as legacy
    (bases frozen, rounded deltas added, residual onto the largest buy).

    Intentionally tilt-unaware: unlike the bare-count path in _reshape_legacy,
    this redeploys budget across subgroups and does not preserve a per-subgroup
    (market-cap tilt) total. A future "more small cap, exclude sectoral" request
    would route here and would NOT preserve the tilt — that's a known gap, not
    a latent bug in this phase.
    """
    # 1. Eligibility: allowed-list, then excluded-list.
    eligible = list(cands)
    if constraints.allowed_categories:
        allowed = set(constraints.allowed_categories)
        eligible = [c for c in eligible if c.sub_category in allowed]
    if constraints.excluded_categories:
        excluded = set(constraints.excluded_categories)
        eligible = [c for c in eligible if c.sub_category not in excluded]
    if not eligible:
        return {c.isin: Decimal(0) for c in cands}    # honest no-op; caller surfaces error

    # 2. Weight targets: raise each named category to its requested share of
    #    the ORIGINAL total. Donors = eligible funds in non-named categories,
    #    scaled down pro-rata; donors flooring at 0 means partial satisfaction,
    #    which the caller narrates honestly from the resulting shares.
    working = {c.isin: c.buy_inr for c in eligible}
    by_isin = {c.isin: c for c in eligible}
    if constraints.category_weight_targets:
        named = set(constraints.category_weight_targets)
        per_cat_deficit: dict[str, Decimal] = {}
        for cat, share in constraints.category_weight_targets.items():
            have = sum(
                (v for k, v in working.items() if by_isin[k].sub_category == cat),
                Decimal(0),
            )
            want = (total * Decimal(str(share))).quantize(_ONE)
            if want > have:
                per_cat_deficit[cat] = want - have
        deficit = sum(per_cat_deficit.values(), Decimal(0))
        donors = [k for k, c in by_isin.items() if c.sub_category not in named]
        donor_total = sum((working[k] for k in donors), Decimal(0))
        take = min(deficit, donor_total)
        for k in donors:
            if donor_total > 0:
                working[k] -= take * working[k] / donor_total
        for cat, cat_deficit in per_cat_deficit.items():
            grant = take * cat_deficit / deficit if deficit > 0 else Decimal(0)
            receivers = [k for k, c in by_isin.items() if c.sub_category == cat]
            recv_total = sum((working[k] for k in receivers), Decimal(0))
            for k in receivers:
                frac = (
                    working[k] / recv_total
                    if recv_total > 0
                    else Decimal(1) / Decimal(len(receivers))
                )
                working[k] += grant * frac

    # 3. Count trim. Protected = weight-target categories present in the plan.
    #    A count below the protected-category count is unsatisfiable as asked:
    #    bump keep_n up to len(protected) — the chat layer discloses the bump.
    live = [c for c in eligible if working[c.isin] > 0]
    ordered = sorted(live, key=lambda c: (c.rank, -working[c.isin]))
    keep = ordered
    if constraints.target_fund_count is not None:
        protected = {
            cat for cat in (constraints.category_weight_targets or {})
            if any(c.sub_category == cat for c in live)
        }
        keep_n = max(1, constraints.target_fund_count, len(protected))
        keep, seen = [], set()
        for c in ordered:              # best fund of each protected category first
            if c.sub_category in protected and c.sub_category not in seen:
                keep.append(c)
                seen.add(c.sub_category)
        for c in ordered:              # fill the rest by rank
            if len(keep) >= keep_n:
                break
            if c not in keep:
                keep.append(c)

    # 4. Same arithmetic form as legacy: frozen base + rounded delta, residual
    #    onto the largest surviving buy, total preserved exactly.
    kept_total = sum((working[c.isin] for c in keep), Decimal(0))
    displaced = total - kept_total
    out: dict[str, Decimal] = {c.isin: Decimal(0) for c in cands}
    for c in keep:
        share = (
            displaced * working[c.isin] / kept_total
            if kept_total > 0
            else displaced / Decimal(len(keep))
        )
        out[c.isin] = working[c.isin] + _round_to_multiple(share, rounding_multiple)
    placed = sum(out.values(), Decimal(0))
    residual = total - placed
    if residual != 0 and keep:
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
    if constraints.category_weight_targets:
        present = {c.sub_category for c in candidates}
        if not (present & set(constraints.category_weight_targets)):
            return response, "weight_category_not_in_plan"

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
