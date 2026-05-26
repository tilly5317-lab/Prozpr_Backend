"""Shared equity-subgroup slider (spec §B.5 step 9, R196-R215).

Single source of truth for the v2 average-based slider that decides which
equity subgroups survive the drop-and-redistribute pass at the end of the
long-term equity build. Used by both the ideal engine
(``asset_allocation_pydantic/steps/step4_long_term.py``) and the practical
engine (``practical_asset_allocation/pipeline.py``).

The slider formula (R198-R199):

    locked_share = (locked_amount / equities_amount) if equities_amount > 0
    first_term   = SLIDER_BASE_PCT
                   − max(0, locked_share − SLIDER_LOCKED_THRESHOLD)
                     × SLIDER_LOCKED_MULTIPLIER
    second_term  = min(SLIDER_AVG_CAP_PCT, average_subgroup_pct)
    min_pct_required = max(first_term, second_term)

In the ideal engine ``locked_amount = 0`` (no ELSS / non-MF holdings exposure),
so ``locked_share = 0``, ``first_term = SLIDER_BASE_PCT = 8`` and the slider
collapses to flat 8% — matching the prior ``MIN_EQUITY_SUBGROUP_SHARE_PCT``
constant. The denominator used for share % is the equity pool that ACTUALLY
funds the subgroups (i.e. post-multi-asset for both engines, and additionally
post-ELSS/non-MF for practical).
"""
from __future__ import annotations

from .utils import round_to_100


# ── Spec §B.5 step 9 slider parameters ────────────────────────────────────────
SLIDER_BASE_PCT: float = 8.0
SLIDER_LOCKED_THRESHOLD: float = 0.20
SLIDER_LOCKED_MULTIPLIER: float = 10.0
SLIDER_AVG_CAP_PCT: float = 3.0


def equity_subgroup_min_pct_required(
    *,
    equities_amount: int,
    locked_amount: int,
    average_subgroup_pct: float,
) -> float:
    """Spec §B.5 step 9 (R198-R199) — v2 average-based slider threshold."""
    if equities_amount > 0:
        locked_share = locked_amount / equities_amount
    else:
        locked_share = 0.0
    first_term = (
        SLIDER_BASE_PCT
        - max(0.0, locked_share - SLIDER_LOCKED_THRESHOLD)
        * SLIDER_LOCKED_MULTIPLIER
    )
    second_term = min(SLIDER_AVG_CAP_PCT, average_subgroup_pct)
    return max(first_term, second_term)


def apply_equity_subgroup_slider(
    subgroup_amounts: dict[str, int],
    *,
    equity_pool: int,
    equities_amount: int = 0,
    locked_amount: int = 0,
) -> tuple[dict[str, int], float, float]:
    """Apply v2 slider to ``subgroup_amounts``: drop subgroups whose share of
    ``equity_pool`` is below ``min_pct_required``, then redistribute the freed
    amount proportionally over the survivors.

    Args:
        subgroup_amounts: ``{equity_subgroup_name: amount}`` before drop.
        equity_pool: equity pool that actually funds these subgroups
            (denominator for share %). Ideal: ``multi.equity_for_subgroups``.
            Practical: ``residual_equity_corpus_final``.
        equities_amount: full long-term equity budget (denominator for the
            locked_share calc). Pass 0 in the ideal engine — locked_share will
            collapse to 0 and the slider locks at SLIDER_BASE_PCT.
        locked_amount: ELSS + non-MF actual (0 in the ideal engine).

    Returns:
        ``(renormalised_subgroup_amounts, min_pct_required, avg_subgroup_pct)``
        — diagnostics on the threshold and the average share the slider saw,
        useful for snapshotting in the practical engine.
    """
    if equity_pool <= 0 or not subgroup_amounts:
        return dict(subgroup_amounts), 0.0, 0.0

    # R198: per-subgroup % of the equity pool.
    pct_by_subgroup: dict[str, float] = {
        sg: (amt * 100.0 / equity_pool) for sg, amt in subgroup_amounts.items()
    }
    non_zero_pcts = [p for p in pct_by_subgroup.values() if p > 0]
    avg_pct = sum(non_zero_pcts) / len(non_zero_pcts) if non_zero_pcts else 0.0

    min_pct_required = equity_subgroup_min_pct_required(
        equities_amount=equities_amount,
        locked_amount=locked_amount,
        average_subgroup_pct=avg_pct,
    )

    # R200-R215: drop below-threshold subgroups; redistribute proportionally.
    surviving = {
        sg: amt for sg, amt in subgroup_amounts.items()
        if pct_by_subgroup.get(sg, 0.0) >= min_pct_required
    }
    freed = sum(
        amt for sg, amt in subgroup_amounts.items() if sg not in surviving
    )
    surviving_sum = sum(surviving.values())

    if freed == 0 or surviving_sum == 0:
        # Either nothing was dropped, or everything was dropped and there's
        # nowhere to redistribute to. Pad zeros and return.
        out = {sg: 0 for sg in subgroup_amounts}
        for sg, amt in surviving.items():
            out[sg] = amt
        return out, min_pct_required, avg_pct

    target_total = surviving_sum + freed
    redistributed = {
        sg: round_to_100(amt + freed * amt / surviving_sum)
        for sg, amt in surviving.items()
    }
    drift = target_total - sum(redistributed.values())
    if drift != 0:
        largest = max(redistributed, key=lambda k: redistributed[k])
        redistributed[largest] += drift

    out = {sg: 0 for sg in subgroup_amounts}
    for sg, amt in redistributed.items():
        out[sg] = amt
    return out, min_pct_required, avg_pct
