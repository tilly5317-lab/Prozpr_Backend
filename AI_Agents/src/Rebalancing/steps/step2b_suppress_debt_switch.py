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

Cash conservation is structural: equal rupees come off both legs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from ..config import REBALANCE_MIN_CHANGE_PCT
from ..models import FundRowAfterStep2, RebalancingComputeRequest

# Debt subgroups treated as one economic sleeve. Product decision: all debt
# funds are assumed to deliver similar returns, so the wrapper choice is worth
# making once at purchase and never revisited with deployed money.
DEBT_POOL: frozenset[str] = frozenset(
    {
        "short_debt",
        "arbitrage",
        "arbitrage_plus_income",
    }
)


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


def apply(
    rows: list[FundRowAfterStep2],
    request: RebalancingComputeRequest,
) -> list[FundRowAfterStep2]:
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
        return rows

    cancelled = {
        **_split_pro_rata(sells, cancel_total, lambda r: -r.diff),
        **_split_pro_rata(buys, cancel_total, lambda r: r.diff),
    }

    out: list[FundRowAfterStep2] = []
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
    return out
