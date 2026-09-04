"""Customer human-override preferences applied to the allocation output.

The ONE enforcement point for standing preferences (spec
2026-09-01-investment-preferences-s1-core). Pure — no I/O, no DB, no LLM.
`asset_allocation_pydantic` must get zero diffs; everything lives here.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from asset_allocation_pydantic.models import (
    AggregatedSubgroupRow,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAssetClassSplit,
    SubgroupBreakdown,
    SubgroupBucketAllocation,
    SubgroupBucketSplit,
)
from asset_allocation_pydantic.steps.step5_aggregation import CANONICAL_SUBGROUP_ORDER
from asset_allocation_pydantic.tables import SUBGROUP_TO_ASSET_CLASS

FROZEN_SUBGROUPS: frozenset[str] = frozenset(
    {"tax_efficient_equities", "non_mf_equities"}
)
SETTABLE_SUBGROUPS: frozenset[str] = frozenset(CANONICAL_SUBGROUP_ORDER)
ASSET_CLASSES = ("equity", "debt", "others")

# SUBGROUP_TO_ASSET_CLASS omits the two frozen practical-only rows; they ARE
# equity for class-total purposes (they never scale — see apply_human_override).
CLASS_OF: dict[str, str] = {
    **SUBGROUP_TO_ASSET_CLASS,
    "tax_efficient_equities": "equity",
    "non_mf_equities": "equity",
}

_SUM_TOLERANCE = 0.5  # percentage points
_MONEY_TOLERANCE = 500.0  # rupees — same conservation tolerance the tests use


def _check_sum_100(name: str, mix: dict[str, float]) -> None:
    total = sum(mix.values())
    if abs(total - 100.0) > _SUM_TOLERANCE:
        raise ValueError(f"{name} must sum to 100 (got {total})")


class HumanOverridePreferences(BaseModel):
    """What the customer asked for, resolved to absolutes app-side.

    ONE subgroup facet: ``subgroup_emphasis`` maps a subgroup to its share
    of its own asset class; an entry of 0 is a hard exclusion (the money
    must leave the row, crossing classes if it is the only row of its
    class). Market-cap asks arrive as emphasis on the beta subgroups
    (large→low_beta, mid→medium_beta, small→high_beta) — one vocabulary,
    matching the PAA output table. extra="forbid" so a stale caller passing
    a removed field fails loud instead of being ignored.
    """

    model_config = {"extra": "forbid"}

    asset_class_requested: Optional[dict[str, float]] = None
    subgroup_emphasis: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "HumanOverridePreferences":
        if self.asset_class_requested is not None:
            if set(self.asset_class_requested) != set(ASSET_CLASSES):
                raise ValueError(
                    "asset_class_requested must carry exactly equity/debt/others"
                )
            _check_sum_100("asset_class_requested", self.asset_class_requested)
        for sg in self.subgroup_emphasis:
            if sg in FROZEN_SUBGROUPS:
                raise ValueError(f"{sg} is frozen and not settable")
            if sg not in SETTABLE_SUBGROUPS:
                raise ValueError(f"unknown subgroup {sg!r}")
        return self

    def is_empty(self) -> bool:
        return (
            self.asset_class_requested is None
            and not self.subgroup_emphasis
        )


BUCKET_COLS = ("emergency", "short_term", "medium_term", "long_term")
PURE_CLASS_THRESHOLD_PCT = 99.0

# Zero-row synthesis: documented static split per class (spec §3.2.6 — static,
# reviewed constants; never derived at runtime).
DEFAULT_CLASS_COMPOSITION: dict[str, dict[str, float]] = {
    "equity": {
        "low_beta_equities": 0.40,
        "medium_beta_equities": 0.40,
        "high_beta_equities": 0.20,
    },
    "debt": {"short_debt": 0.60, "arbitrage": 0.40},
    "others": {"gold_commodities": 1.0},
}


class HumanOverrideApplied(BaseModel):
    requested: Optional[dict[str, float]] = None
    achieved: dict[str, float]
    shortfall_reason: Optional[str] = None


def _scaled(row: AggregatedSubgroupRow, f: float) -> AggregatedSubgroupRow:
    return row.model_copy(update={
        "emergency": row.emergency * f, "short_term": row.short_term * f,
        "medium_term": row.medium_term * f, "long_term": row.long_term * f,
        "total": row.total * f,
    })


def _grown_long_term(row: AggregatedSubgroupRow, add: float) -> AggregatedSubgroupRow:
    return row.model_copy(update={
        "long_term": row.long_term + add, "total": row.total + add,
    })


def _carved_contribution(r, multi_asset_composition) -> dict[str, float]:
    """A row's contribution per asset class in CARVED basis: multi_asset is
    split by its composition (the customer sees it carved); every other row
    counts wholly under its CLASS_OF class."""
    if r.subgroup == "multi_asset" and multi_asset_composition is not None:
        c = multi_asset_composition
        return {
            "equity": r.total * c.equity_pct / 100.0,
            "debt": r.total * c.debt_pct / 100.0,
            "others": r.total * c.others_pct / 100.0,
        }
    return {CLASS_OF.get(r.subgroup, "others"): r.total}


def _apply_class_targets(
    rows: list[AggregatedSubgroupRow],
    requested: dict[str, float],
    multi_asset_composition=None,
) -> tuple[list[AggregatedSubgroupRow], bool]:
    """Move the class mix to the requested target in CARVED basis (the numbers
    the customer sees — multi_asset split by its composition), shrinking
    uniformly across buckets and growing into long_term only (spec §9.1).

    Frozen rows AND multi_asset are FIXED multi-class contributors: their
    carved contribution is subtracted from each class target, and the pure
    (non-frozen, non-multi_asset) rows are the adjustment lever. This keeps
    requested and achieved in the same (carved) basis — targeting row basis
    while the customer reads carved silently swallowed the whole tilt step.
    Returns (rows, floors_bound); floors_bound True when a fixed contribution
    genuinely constrained the requested mix (gates the shortfall message)."""
    grand = sum(r.total for r in rows)
    if grand <= 0:
        return rows, False

    pure_class = next(
        (c for c, p in requested.items() if p >= PURE_CLASS_THRESHOLD_PCT), None
    )
    drop_multi_asset = pure_class == "equity"

    # multi_asset is a FIXED carved multi-class contributor only when we have
    # its composition (production always does — the pipeline defaults 65/25/10);
    # without one there's no carved/row distinction, so it stays a migratable
    # equity row (legacy behaviour). Dropped entirely for a pure-equity ask.
    multi_fixed = multi_asset_composition is not None and not drop_multi_asset

    # Fixed carved contributions (frozen rows always; multi_asset when fixed).
    fixed_by_class: dict[str, float] = {}
    migratable_by_class: dict[str, float] = {}
    for r in rows:
        if r.subgroup in FROZEN_SUBGROUPS or (
            r.subgroup == "multi_asset" and multi_fixed
        ):
            for cls, amt in _carved_contribution(r, multi_asset_composition).items():
                fixed_by_class[cls] = fixed_by_class.get(cls, 0.0) + amt
        elif drop_multi_asset and r.subgroup == "multi_asset":
            continue  # its budget joins the pool (target math below)
        else:
            cls = CLASS_OF.get(r.subgroup, "others")
            migratable_by_class[cls] = migratable_by_class.get(cls, 0.0) + r.total

    frozen_by_class = fixed_by_class  # (name kept below for floors_bound logic)

    # Migratable target per class = requested carved share of the FULL grand
    # total, minus what the fixed contributors already supply (floored at 0).
    migratable_target: dict[str, float] = {}
    for cls in ASSET_CLASSES:
        migratable_target[cls] = max(
            0.0, grand * requested[cls] / 100.0 - fixed_by_class.get(cls, 0.0)
        )
    # Conservation: rescale migratable targets to the migratable pool exactly
    # (frozen floors can make raw targets sum below the pool).
    pool = sum(migratable_by_class.values()) + (
        sum(r.total for r in rows if drop_multi_asset and r.subgroup == "multi_asset")
    )
    target_sum = sum(migratable_target.values())
    if target_sum > 0:
        migratable_target = {
            c: t * pool / target_sum for c, t in migratable_target.items()
        }

    out: list[AggregatedSubgroupRow] = []
    grow_delta: dict[str, float] = {}
    for cls in ASSET_CLASSES:
        cur = migratable_by_class.get(cls, 0.0)
        tgt = migratable_target.get(cls, 0.0)
        grow_delta[cls] = tgt - cur

    class_row_totals: dict[str, float] = dict(migratable_by_class)
    for r in rows:
        if r.subgroup in FROZEN_SUBGROUPS:
            out.append(r)
            continue
        if drop_multi_asset and r.subgroup == "multi_asset":
            out.append(_scaled(r, 0.0))  # budget pooled into equity above
            continue
        if multi_fixed and r.subgroup == "multi_asset":
            out.append(r)  # fixed contributor — carved parts already in targets
            continue
        cls = CLASS_OF.get(r.subgroup, "others")
        delta = grow_delta[cls]
        cur_cls = class_row_totals.get(cls, 0.0)
        if delta >= 0 or cur_cls <= 0:
            share = (r.total / cur_cls) if cur_cls > 0 else 0.0
            out.append(_grown_long_term(r, delta * share) if delta > 0 else r)
        else:
            out.append(_scaled(r, (cur_cls + delta) / cur_cls))

    # Zero-row synthesis: a growing class with no migratable value at all.
    # step5 emits rows for EVERY canonical subgroup (zero-total rows exist), so
    # UPDATE an existing zero row in place; append only if truly absent —
    # otherwise we'd create duplicate subgroup rows that the view rebuild
    # silently collapses.
    floors_bound = any(
        grand * requested[c] / 100.0 - frozen_by_class.get(c, 0.0) < -0.5
        for c in ASSET_CLASSES
    ) or (target_sum > 0 and abs(pool / target_sum - 1.0) > 0.01)
    by_subgroup = {r.subgroup: i for i, r in enumerate(out)}
    for cls, delta in grow_delta.items():
        if delta > 0 and class_row_totals.get(cls, 0.0) <= 0:
            for sg, share in DEFAULT_CLASS_COMPOSITION[cls].items():
                add = delta * share
                if sg in by_subgroup:
                    i = by_subgroup[sg]
                    out[i] = _grown_long_term(out[i], add)
                else:
                    row = AggregatedSubgroupRow(
                        subgroup=sg, emergency=0.0, short_term=0.0,
                        medium_term=0.0, long_term=add, total=add,
                    )
                    by_subgroup[sg] = len(out)
                    out.append(row)
    return out, floors_bound


def _rebuild_subgroups_view(rows: list[AggregatedSubgroupRow]) -> SubgroupBreakdown:
    """Rebuild the per-bucket subgroup breakdown from reshaped rows so it
    agrees with `aggregated_subgroups` (spec F1 — every view agrees). The
    practical engine's convention: `planned` and `recommended` are identical
    lists, both reflecting the one reshaped view."""
    splits: list[SubgroupBucketSplit] = []
    for bucket in BUCKET_COLS:
        vals = [
            (r.subgroup, getattr(r, bucket)) for r in rows if getattr(r, bucket) > 0
        ]
        bucket_total = sum(v for _, v in vals)
        subgroups = [
            SubgroupBucketAllocation(
                subgroup=sg, amount=int(round(v)),
                pct_of_bucket=(v * 100.0 / bucket_total) if bucket_total else 0.0,
            )
            for sg, v in vals
        ]
        splits.append(SubgroupBucketSplit(bucket=bucket, subgroups=subgroups))
    return SubgroupBreakdown(planned=splits, recommended=splits)


def _rebuild_views(output, rows, multi_asset_composition):
    """Recompute bucket subgroup_amounts + asset_class_breakdown from rows so
    every view agrees with the reshaped table (spec F1)."""
    comp = multi_asset_composition  # None → multi_asset counts wholly as equity

    def split(sub_totals: dict[str, float]) -> tuple[float, float, float]:
        eq = dt = oth = 0.0
        for sg, amt in sub_totals.items():
            if sg == "multi_asset" and amt > 0 and comp is not None:
                eq += amt * comp.equity_pct / 100.0
                oth += amt * comp.others_pct / 100.0
                dt += amt * comp.debt_pct / 100.0
                continue
            cls = CLASS_OF.get(sg, "others")
            if cls == "equity":
                eq += amt
            elif cls == "debt":
                dt += amt
            else:
                oth += amt
        return eq, dt, oth

    per_bucket = []
    for bucket in BUCKET_COLS:
        sub = {r.subgroup: getattr(r, bucket) for r in rows if getattr(r, bucket) > 0}
        eq, dt, oth = split(sub)
        tot = eq + dt + oth
        per_bucket.append(BucketAssetClassSplit(
            bucket=bucket, equity=int(round(eq)), debt=int(round(dt)),
            others=int(round(oth)),
            equity_pct=(eq * 100.0 / tot) if tot else 0.0,
            debt_pct=(dt * 100.0 / tot) if tot else 0.0,
            others_pct=(oth * 100.0 / tot) if tot else 0.0,
        ))
    eq_t = sum(b.equity for b in per_bucket)
    dt_t = sum(b.debt for b in per_bucket)
    oth_t = sum(b.others for b in per_bucket)
    grand = eq_t + dt_t + oth_t
    block = AssetClassSplitBlock(
        per_bucket=per_bucket, equity_total=eq_t, debt_total=dt_t,
        others_total=oth_t,
        equity_total_pct=(eq_t * 100.0 / grand) if grand else 0.0,
        debt_total_pct=(dt_t * 100.0 / grand) if grand else 0.0,
        others_total_pct=(oth_t * 100.0 / grand) if grand else 0.0,
    )
    old_subgroups = output.asset_class_breakdown.subgroups
    new_subgroups = (
        _rebuild_subgroups_view(rows) if old_subgroups is not None else None
    )
    breakdown = AssetClassBreakdown(
        planned=output.asset_class_breakdown.planned,
        recommended=block,
        recommended_sum_matches_grand_total=True,
        subgroups=new_subgroups,
    )
    new_buckets = []
    for b in output.bucket_allocations:
        col = b.bucket
        sub = {
            r.subgroup: int(round(getattr(r, col)))
            for r in rows
            if getattr(r, col) > 0
        }
        new_buckets.append(b.model_copy(update={
            "subgroup_amounts": sub,
            "allocated_amount": float(sum(sub.values())),
        }))
    return output.model_copy(update={
        "aggregated_subgroups": rows,
        "bucket_allocations": new_buckets,
        "asset_class_breakdown": breakdown,
        # No rounding in the step (existing sleeve-tilt precedent — downstream
        # consumers round themselves); recompute the multiples-of-100 flag
        # honestly instead of inheriting a now-false claim.
        "all_amounts_in_multiples_of_100": all(
            round(getattr(r, col)) % 100 == 0
            for r in rows
            for col in BUCKET_COLS
        ),
    }), block


def _is_migratable(subgroup: str) -> bool:
    """A row a within-class subgroup preference may draw from or grow into.
    Frozen rows (ELSS / direct stock) AND multi_asset are excluded:
    multi_asset spans classes (carved), so moving it to satisfy a within-
    class subgroup ask would shift the carved CLASS mix the customer set —
    the same reason it is a fixed contributor for class targets (ruling 10).
    A subgroup can still be the direct SUBJECT of an exclusion; it just
    can't be a donor/recipient for another subgroup's ask."""
    return subgroup not in FROZEN_SUBGROUPS and subgroup != "multi_asset"


def _redistribute_within_class(rows, from_subgroup, multi_asset_composition=None):
    """Zero ``from_subgroup``; move each bucket column to same-class migratable
    survivors pro-rata per column (falls back to all migratable rows if the
    class dies). A multi_asset victim is split by its COMPOSITION — its
    equity/debt/others slices go to that class's peers — so excluding a
    balanced fund does not convert its debt and gold into equity."""
    victim = next((r for r in rows if r.subgroup == from_subgroup), None)
    if victim is None or victim.total <= 0:
        return rows
    all_migratable = [
        r for r in rows
        if r.subgroup != from_subgroup and _is_migratable(r.subgroup) and r.total > 0
    ]
    if not all_migratable:
        return rows
    portions = {
        cls: amt / victim.total
        for cls, amt in _carved_contribution(victim, multi_asset_composition).items()
        if amt > 0
    }
    updated: dict[str, dict[str, float]] = {}

    def cells(r):
        return updated.setdefault(r.subgroup, dict(
            emergency=r.emergency, short_term=r.short_term,
            medium_term=r.medium_term, long_term=r.long_term,
        ))

    for cls, frac in portions.items():
        survivors = [
            r for r in all_migratable if CLASS_OF.get(r.subgroup, "others") == cls
        ] or all_migratable
        for col in BUCKET_COLS:
            amount = getattr(victim, col) * frac
            if amount <= 0:
                continue
            col_total = sum(getattr(r, col) for r in survivors)
            if col_total > 0:
                for r in survivors:
                    cells(r)[col] += amount * getattr(r, col) / col_total
            else:
                # No survivor holds this column — column-preserving still wins:
                # split into the SAME column pro-rata by row total, never
                # relabel near-term money as long_term.
                survivor_total = sum(r.total for r in survivors)
                for r in survivors:
                    cells(r)[col] += amount * r.total / survivor_total
    out = []
    for r in rows:
        if r.subgroup == from_subgroup:
            out.append(_scaled(r, 0.0))
        elif r.subgroup in updated:
            u = updated[r.subgroup]
            out.append(r.model_copy(update={**u, "total": sum(u.values())}))
        else:
            out.append(r)
    return out


def _apply_exclusions(rows, exclusions, multi_asset_composition=None):
    for sg in exclusions:
        rows = _redistribute_within_class(rows, sg, multi_asset_composition)
    return rows


def _apply_emphasis(rows, emphasis):
    """One-shot per-class solve (order-independent). Each ask is the row's
    target share of its OWN class. Asks summing over 100% scale down
    proportionally; unconstrained rows share the remainder pro-rata by
    current size. COLUMN-PRESERVING at class level: shrinkers release money
    per bucket column into a pool, growers receive from that pool in the
    same columns, so AINV's column weighting and the bucket views keep
    meaning. Returns (rows, notes) — notes feed the customer-facing
    shortfall disclosure when asks had to be rescaled."""
    notes: list[str] = []
    by_class: dict[str, dict[str, float]] = {}
    for sg, share in emphasis.items():
        by_class.setdefault(CLASS_OF.get(sg, "others"), {})[sg] = share

    for cls, asks in by_class.items():
        cls_rows = [
            r for r in rows
            if CLASS_OF.get(r.subgroup, "others") == cls
            and _is_migratable(r.subgroup)
        ]
        cls_total = sum(r.total for r in cls_rows)
        if cls_total <= 0:
            # No money in this class to reshape — the category emphasis can't
            # be applied. Disclose rather than silently drop it.
            wanted = [sg for sg, v in asks.items() if v > 0]
            if wanted:
                notes.append(
                    f"no {cls} holdings to apply your "
                    f"{', '.join(wanted)} preference to"
                )
            continue

        ask_sum = sum(asks.values())
        scale = 1.0
        if ask_sum > 100.0:
            scale = 100.0 / ask_sum
            notes.append(
                f"your {cls} choices total {ask_sum:.0f}% — "
                "scaled proportionally to fit"
            )
        targets = {sg: cls_total * share * scale / 100.0 for sg, share in asks.items()}

        unconstrained = [r for r in cls_rows if r.subgroup not in targets]
        uncon_total = sum(r.total for r in unconstrained)
        remainder = cls_total - sum(targets.values())
        if uncon_total <= 0:
            # No unrestricted row can absorb the remainder — the asks must
            # cover the whole class. (E.g. an ask on the class's only row.)
            tsum = sum(targets.values())
            if tsum > 0 and abs(tsum - cls_total) > 1.0:
                targets = {sg: t * cls_total / tsum for sg, t in targets.items()}
                notes.append(
                    f"no unrestricted {cls} category left — your choices "
                    "were rescaled to cover the class"
                )
            remainder = 0.0

        idx = {r.subgroup: r for r in rows}
        for sg in targets:
            if sg not in idx:
                idx[sg] = AggregatedSubgroupRow(
                    subgroup=sg, emergency=0.0, short_term=0.0,
                    medium_term=0.0, long_term=0.0, total=0.0,
                )
                rows = [*rows, idx[sg]]

        target_by_sg = dict(targets)
        f_uncon = (remainder / uncon_total) if uncon_total > 0 else 0.0
        for r in unconstrained:
            target_by_sg[r.subgroup] = r.total * f_uncon

        freed = {c: 0.0 for c in BUCKET_COLS}
        growth: dict[str, float] = {}
        for sg, tgt in target_by_sg.items():
            r = idx[sg]
            if tgt < r.total - 1e-9:
                f = tgt / r.total if r.total > 0 else 0.0
                for c in BUCKET_COLS:
                    freed[c] += getattr(r, c) * (1.0 - f)
                idx[sg] = _scaled(r, f)
            elif tgt > r.total + 1e-9:
                growth[sg] = tgt - r.total
        grow_sum = sum(growth.values())
        if grow_sum > 0:
            for sg, g in growth.items():
                r = idx[sg]
                gained = {c: freed[c] * g / grow_sum for c in BUCKET_COLS}
                idx[sg] = r.model_copy(update={
                    **{c: getattr(r, c) + gained[c] for c in BUCKET_COLS},
                    "total": r.total + sum(gained.values()),
                })
        rows = [idx.get(r.subgroup, r) for r in rows]

    return rows, notes


def _assert_conserved(rows: list[AggregatedSubgroupRow], original_total: float) -> None:
    """Spec §3.2.7: the reshape must never lose (or invent) money, and never
    leave a negative cell. Fail loud — a silent leak here is a customer's
    money quietly vanishing from their plan."""
    total = sum(r.total for r in rows)
    if abs(total - original_total) > _MONEY_TOLERANCE:
        raise ValueError(
            "human override reshape did not conserve the grand total: "
            f"rows sum to {total:.2f}, expected {original_total:.2f}"
        )
    for r in rows:
        for col in (*BUCKET_COLS, "total"):
            val = getattr(r, col)
            # Sign is not a conservation rounding question — a cell should
            # never go meaningfully negative, so guard with a tight epsilon
            # rather than the ₹500 conservation tolerance.
            if val < -1.0:
                raise ValueError(
                    f"human override reshape produced a negative cell: "
                    f"{r.subgroup}.{col} = {val:.2f}"
                )


def apply_human_override(output, prefs, multi_asset_composition=None):
    """THE enforcement point. Always invoked; strict no-op when prefs is None
    or empty (golden-test guarantee). Pure — returns a new output."""
    if prefs is None or prefs.is_empty():
        return output, None

    rows = [r.model_copy() for r in output.aggregated_subgroups]
    original_total = sum(r.total for r in rows)
    emergency_before = sum(r.emergency for r in rows)
    floors_bound = False
    if prefs.asset_class_requested:
        rows, floors_bound = _apply_class_targets(
            rows, prefs.asset_class_requested, multi_asset_composition
        )
    zeroed = [sg for sg, v in prefs.subgroup_emphasis.items() if v <= 0]
    positive = {sg: v for sg, v in prefs.subgroup_emphasis.items() if v > 0}
    if zeroed:
        rows = _apply_exclusions(rows, zeroed, multi_asset_composition)
    emphasis_notes: list[str] = []
    if positive:
        rows, emphasis_notes = _apply_emphasis(rows, positive)
    _assert_conserved(rows, original_total)
    reshaped, block = _rebuild_views(output, rows, multi_asset_composition)
    achieved = {
        "equity": block.equity_total_pct,
        "debt": block.debt_total_pct,
        "others": block.others_total_pct,
    }
    # Shortfall detection compares the CARVED achieved (what the customer
    # sees, same basis the ask is expressed in) to the request. A normal
    # class move now lands exactly, so a gap here means something genuinely
    # constrained it: frozen floors (floors_bound), or the customer's own
    # exclusions relocating money out of a class. Both must be disclosed.
    shortfall = None
    if prefs.asset_class_requested:
        gaps = [
            f"{c}: asked {prefs.asset_class_requested[c]:.0f}%, "
            f"landed {achieved[c]:.1f}%"
            for c in ASSET_CLASSES
            if abs(achieved[c] - prefs.asset_class_requested[c]) > 2.0
        ]
        if gaps:
            lead = (
                "immovable holdings (ELSS lock-in / direct stock) limit the move"
                if floors_bound
                else "your category choices moved the mix off the class target"
            )
            shortfall = lead + " — " + "; ".join(gaps)
    # I1: the class shrink cuts debt/others uniformly across buckets, including
    # the emergency buffer (full-honor, spec §9.1) — disclose the cut.
    emergency_after = sum(r.emergency for r in rows)
    if (
        prefs.asset_class_requested
        and emergency_before > 0
        and emergency_after < emergency_before - _MONEY_TOLERANCE
    ):
        cut_pct = (emergency_before - emergency_after) * 100.0 / emergency_before
        emphasis_notes = [
            *emphasis_notes,
            f"this reduces your emergency buffer by {cut_pct:.0f}%",
        ]
    if emphasis_notes:
        shortfall = "; ".join(filter(None, [shortfall, *emphasis_notes]))
    return reshaped, HumanOverrideApplied(
        requested=prefs.asset_class_requested,
        achieved=achieved,
        shortfall_reason=shortfall,
    )
