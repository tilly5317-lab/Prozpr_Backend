"""Step 2b — suppress debt-for-debt switching.

Design note: `docs/superpowers/specs/2026-07-18-debt-switch-netting-design.md`.

The allocation engine picks a debt wrapper from the customer's tax rate and the
goal's remaining tenure. As either drifts the chosen wrapper changes, one
subgroup's target falls and another's rises, and rebalancing executes a switch
that leaves total debt exposure unchanged and costs real tax.

This step cancels those matched sell/buy *intents* before step3 classifies
gains, so the tax arithmetic runs once, forward, on the corrected picture. It
runs on intents rather than trades because step4's `scale` and `floor_to_step`
are not invertible — see the design note's "Rejected designs".

Equal rupees come off both legs of a match, but do NOT read that as the thing
guaranteeing cash conservation. `Σbuys <= Σsells` is enforced downstream by
step4 re-deriving `scale` from *realised* sells and flooring each buy
(`step4:309-322`); the residual absorption below deliberately cancels more sell
than the matched amount, so this step's symmetry is not exact and
`net_cash_flow_inr` does move. Verified empirically across all 5 sim profiles.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from ..config import (
    DEBT_NETTING_MODE,
    DEBT_SWITCH_NETTING_ENABLED,
    REBALANCE_MIN_CHANGE_PCT,
)
from ..models import (
    FundRowAfterStep2,
    RebalancingComputeRequest,
    RebalancingWarning,
    WarningCode,
)
from ..tables import DEBT_NETTING_POOL as DEBT_POOL, effective_cap_for


def _split_pro_rata(
    rows: list[FundRowAfterStep2],
    total: Decimal,
    weight: Callable[[FundRowAfterStep2], Decimal],
) -> dict[str, Decimal]:
    """Split `total` across `rows` in proportion to `weight`.

    The last row absorbs the rounding remainder so the shares sum to `total`
    exactly — conservation depends on it.
    """
    total_weight = sum((weight(r) for r in rows), Decimal(0))
    if total_weight <= 0 or total <= 0:
        return {r.isin: Decimal(0) for r in rows}

    shares: dict[str, Decimal] = {}
    assigned = Decimal(0)
    for r in rows[:-1]:
        share = total * weight(r) / total_weight
        shares[r.isin] = share
        assigned += share
    shares[rows[-1].isin] = total - assigned
    return shares


def _cap_spill_buy_reductions(
    buys: list[FundRowAfterStep2],
    cancel_total: Decimal,
    request: RebalancingComputeRequest,
) -> dict[str, Decimal]:
    """Reduce the buy side by re-spilling the surviving demand down the ladder.

    Pro-rata shrinks every buyer in proportion to its own demand — a rule that
    exists nowhere else in the engine. This reuses step1's philosophy instead:
    whatever buy demand survives the cancellation is walked down the rank ladder,
    best fund first, under the per-fund cap.

    The surviving budget is deliberately `buy_total - cancel_total` rather than
    anything derived from the subgroup target. Deriving it from the target lets
    the recomputed budget RESURRECT demand the cancellation just removed, which
    breaks conservation (measured: ₹57L of sell cancelled against only ₹49L of
    buy). Defining it as the leftover makes `sum(reductions) == cancel_total`
    true by construction.

    Returns the REDUCTION per buyer isin, so the caller applies it exactly as it
    applies the pro-rata result.
    """
    corpus = request.total_corpus
    remaining = max(sum((r.diff for r in buys), Decimal(0)) - cancel_total, Decimal(0))

    out: dict[str, Decimal] = {}
    for r in sorted(buys, key=lambda x: (x.rank, x.isin)):
        cap_amount = effective_cap_for(r.asset_subgroup, corpus)
        # Never grant more than step1 wanted here, nor more than the fund may hold.
        grant = min(remaining, cap_amount, r.diff)
        remaining -= grant
        out[r.isin] = r.diff - grant
    return out


def apply(
    rows: list[FundRowAfterStep2],
    request: RebalancingComputeRequest,
) -> tuple[list[FundRowAfterStep2], list[RebalancingWarning]]:
    """Returns (rows, warnings). Warning shape matches steps 1, 2 and 4."""
    if not DEBT_SWITCH_NETTING_ENABLED:
        return rows, []

    corpus = request.total_corpus
    threshold_factor = Decimal(str(REBALANCE_MIN_CHANGE_PCT))

    debt = [r for r in rows if r.asset_subgroup in DEBT_POOL]

    # Eligibility mirrors step4's own pools (step4:275-277) so we never cancel
    # intent step4 would not have executed. Two sells are carved out:
    #   - `exit_flag`: a bad fund is still a bad fund.
    #   - `rank == 0`: off-list NEUTRAL holdings migrate their LT portion into
    #     the recommended fund. `input_builder` sets their target to the SHORT
    #     -term value, so they carry `diff = -lt_value` and look like ordinary
    #     sells. Suppressing them would kill the migration — and `exit_flag` is
    #     dead in production, so this is the only carve-out that actually runs.
    sells = [
        r
        for r in debt
        if r.worth_to_change and r.diff < 0 and not r.exit_flag and r.rank != 0
    ]
    buys = [r for r in debt if r.worth_to_change and r.diff > 0 and r.is_recommended]

    sell_total = sum((-r.diff for r in sells), Decimal(0))
    buy_total = sum((r.diff for r in buys), Decimal(0))

    # Forced sells execute unconditionally and before any buy-demand gate
    # (step4:294-301), so their proceeds already have a destination. Reserve
    # that much buy demand out of the match or the cash strands and surfaces as
    # negative `net_cash_flow_inr` (step6:294).
    #
    # Debt-scoped for now. When the equity policy lands this reserve has to
    # become global — an equity cancellation in another subgroup can strand a
    # debt reserve. See the #1 design note, §4.
    forced_proceeds = sum(
        (r.present_allocation_inr for r in debt if r.exit_flag), Decimal(0)
    )
    buy_capacity = max(buy_total - forced_proceeds, Decimal(0))

    cancel_total = min(sell_total, buy_capacity)

    if cancel_total <= 0:
        return rows, []

    cancelled = {**_split_pro_rata(sells, cancel_total, lambda r: -r.diff)}
    if DEBT_NETTING_MODE == "cap_spill":
        cancelled.update(_cap_spill_buy_reductions(buys, cancel_total, request))
    else:
        cancelled.update(_split_pro_rata(buys, cancel_total, lambda r: r.diff))

    out: list[FundRowAfterStep2] = []
    # Accumulated from what actually happened, not from `cancel_total` — the
    # residual absorption below cancels MORE than the match, so reporting the
    # match would understate the figure exactly as the adjustment field once did.
    sell_avoided = Decimal(0)
    untraded: list[str] = []
    left_above_cap = Decimal(0)

    for r in rows:
        amount = cancelled.get(r.isin, Decimal(0))
        if amount <= 0:
            out.append(r)
            continue

        # A sell intent shrinks by raising its target toward what is held; a
        # buy intent shrinks by lowering it. Either way the target moves in
        # lockstep so `diff == final_target_amount - present` still holds.
        direction = Decimal(1) if r.diff < 0 else Decimal(-1)
        target_move = direction * amount
        final_target = r.final_target_amount + target_move
        diff = final_target - r.present_allocation_inr

        # Re-gate against the NEW target — the threshold scale is
        # `max(target, present)` and it moved too. The `diff != 0` guard covers
        # step2's zero-scale edge: with target and present both 0 the threshold
        # is 0 and `abs(0) >= 0` is True, which would leave a fully-netted row
        # looking tradeable and undercount `funds_held_count` (step6:288).
        scale = max(final_target, r.present_allocation_inr)
        worth_to_change = r.exit_flag or (
            diff != 0 and abs(diff) >= scale * threshold_factor
        )

        # Absorb a residual too small to clear the materiality bar. Step4's
        # pools require `worth_to_change`, so such a leftover would be dropped
        # there anyway — silently, and after we had already recorded a smaller
        # target move than actually took effect. Cancelling it here keeps
        # `netted_target_adjustment_inr` equal to the real change, so step6's
        # `goal_target_inr` still matches what the customer ends up holding.
        # Not selling a sub-threshold sliver is the right outcome either way;
        # this only makes it deliberate and correctly booked.
        if not worth_to_change and diff != 0:
            target_move -= diff
            final_target = r.present_allocation_inr
            diff = Decimal(0)

        if direction > 0:
            sell_avoided += target_move
            # Declining to trim can leave a fund above its per-fund cap. Track
            # it so the customer-facing message can say so — "why is so much
            # sitting in one fund" is a likelier question than "what tax did
            # you save", and there is no per-trade rationale to carry it
            # because we have deliberately produced an *inaction*.
            cap_amount = effective_cap_for(r.asset_subgroup, corpus)
            if final_target > cap_amount:
                left_above_cap = max(left_above_cap, final_target)
        if diff == 0:
            untraded.append(r.isin)

        out.append(
            FundRowAfterStep2(
                **{
                    **r.model_dump(),
                    "final_target_amount": final_target,
                    "final_target_pct": (
                        float(final_target / corpus * Decimal(100))
                        if corpus > 0
                        else 0.0
                    ),
                    "diff": diff,
                    "worth_to_change": worth_to_change,
                    "netted_target_adjustment_inr": (
                        r.netted_target_adjustment_inr + target_move
                    ),
                }
            )
        )

    # `common.format_inr_indian` is the project standard; imported locally to
    # keep this module's top-level deps to config + models, as step6 does.
    from common import format_inr_indian  # type: ignore[import-not-found]

    # Customer-facing: this renders as a heads-up bullet (formatter.py:251), so
    # it follows the tone standard in rationales.py — matter-of-fact, no jargon,
    # no blame. The concentration clause is appended only when a protected fund
    # genuinely ends above its cap; claiming it otherwise would be false.
    message = (
        f"Kept {format_inr_indian(int(sell_avoided))} in your existing debt "
        f"funds rather than moving it into similar ones — switching would have "
        f"triggered a tax bill without changing what you actually hold."
    )
    if left_above_cap > 0:
        message += (
            " This does leave more in a single fund than we would usually "
            "hold; new investments will bring that back into line rather "
            "than selling now and paying the tax."
        )

    warning = RebalancingWarning(
        code=WarningCode.DEBT_SWITCH_SUPPRESSED,
        message=message,
        affected_isins=sorted(untraded),
    )
    return out, [warning]
