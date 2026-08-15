"""Step 2c — suppress optional sells we don't want to place.

Runs after step2b (debt netting) and before step3, so it sees the settled sell
intents. It cancels an optional trim by raising its target back to what is held:

* Sub-0.5%-of-portfolio trims (feedback 2026-08): a sliver is not worth selling
  unless it liquidates the whole fund.

Force-exits and full exits (final target 0) are never suppressed. Cancelling a
sell records the signed target move on `netted_target_adjustment_inr` so step6's
subgroup totals still reconcile — the same bookkeeping step2b uses.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import REBALANCE_MIN_SELL_PORTFOLIO_PCT
from ..models import (
    FundRowAfterStep2,
    RebalancingComputeRequest,
    RebalancingWarning,
)


def _cancel_sell(row: FundRowAfterStep2, corpus: Decimal) -> FundRowAfterStep2:
    """Raise an optional sell's target back to what's held (no sell).

    Records the signed move on `netted_target_adjustment_inr` so that
    `target_amount_pre_cap + adjustment == final_target_amount` still holds for
    step6's subgroup reconciliation.
    """
    move = row.present_allocation_inr - row.final_target_amount
    return row.model_copy(
        update={
            "final_target_amount": row.present_allocation_inr,
            "final_target_pct": (
                float(row.present_allocation_inr / corpus * Decimal(100))
                if corpus > 0
                else 0.0
            ),
            "diff": Decimal(0),
            "worth_to_change": False,
            "netted_target_adjustment_inr": row.netted_target_adjustment_inr + move,
        }
    )


def apply(
    rows: list[FundRowAfterStep2],
    request: RebalancingComputeRequest,
) -> tuple[list[FundRowAfterStep2], list[RebalancingWarning]]:
    corpus = request.total_corpus
    min_sell = corpus * Decimal(str(REBALANCE_MIN_SELL_PORTFOLIO_PCT))

    out: list[FundRowAfterStep2] = []
    for r in rows:
        optional_sell = r.worth_to_change and r.diff < 0 and not r.exit_flag
        if not optional_sell:
            out.append(r)
            continue
        full_exit = r.final_target_amount <= 0  # selling the whole fund
        # Feedback 2: never trim a fund bought since the last rebalance.
        recently_bought = r.bought_since_last_rebalance
        # Feedback 3: a sub-0.5% trim isn't worth placing, unless it's a full exit.
        tiny_trim = (not full_exit) and abs(r.diff) < min_sell
        if recently_bought or tiny_trim:
            out.append(_cancel_sell(r, corpus))
        else:
            out.append(r)
    return out, []
