"""Step 5 — loss-offset top-up pass.

Spreadsheet refs: cols AH (`stcg_budget_remaining_after_pass1`), AL (`stcg_offset_amount`),
AM–AP.

Logic
-----
1. `available_offset = carryforward_st_loss + realised_st_loss_pass_1`
   — SHORT-term losses only. Under Indian capital-gains rules a long-term
   capital loss may be set off only against LTCG, never against STCG, so LT
   losses (carryforward or realised this pass) are deliberately excluded here.
2. `stcg_offset_amount (portfolio) = min(realised_stcg_pass_1, available_offset)`.
3. `extra_headroom = available_offset - stcg_net_off` — additional STCG
   we can now realise without tax cost.
4. Re-run the sell logic on rows that were ST-bound undersold in pass-1,
   capped by `extra_headroom`. Convert the unlocked portion into
   `pass2_sell_amount`.
5. `final_holding_amount = holding_after_initial_trades − pass2_sell_amount`.

Per-row `stcg_offset_amount` is allocated proportionally to the row's pass-1
STCG realisation. Portfolio total is exposed via `RebalancingTotals`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..models import (
    FundRowAfterStep4,
    FundRowAfterStep5,
    RebalancingComputeRequest,
)
from .step4_initial_trades_under_stcg_cap import _sell_from_row


def apply(
    rows: list[FundRowAfterStep4],
    request: RebalancingComputeRequest,
) -> list[FundRowAfterStep5]:
    realised_st_loss_p1 = sum(
        (-r.pass1_realised_stcg for r in rows if r.pass1_realised_stcg < 0),
        Decimal(0),
    )
    # STCG may be set off ONLY by short-term capital losses (brought-forward +
    # realised this pass). Long-term losses are excluded: per Indian IT rules an
    # LT capital loss can offset only LTCG, never STCG. (The engine does not yet
    # model LT-loss set-off against LTCG — see carryforward_lt_loss_inr in
    # models.py — so carryforward_lt_loss_inr is intentionally unused here.)
    available_offset = request.carryforward_st_loss_inr + realised_st_loss_p1

    realised_stcg_p1 = sum(
        (r.pass1_realised_stcg for r in rows if r.pass1_realised_stcg > 0),
        Decimal(0),
    )
    stcg_net_off_total = min(realised_stcg_p1, available_offset)
    extra_headroom: Decimal = available_offset - stcg_net_off_total
    if extra_headroom < 0:
        extra_headroom = Decimal(0)

    # Pass-2: convert ST-bound undersells into actual sells using extra headroom.
    # Only forced-exit rows are eligible — optional (recommended-trim) rows
    # are LT-only under the new "STCG only on force-exit" rule, so their
    # `pass1_undersell_*` reflects a deliberate skip of the ST bucket, not
    # an STCG-cap block that loss-offset headroom can unlock.
    sold_pass2: dict[str, Decimal] = {r.isin: Decimal(0) for r in rows}
    candidates = [
        r for r in rows if r.exit_flag and r.pass1_undersell_due_to_stcg_cap > 0
    ]
    candidates.sort(
        key=lambda r: (
            float(r.pass1_blocked_stcg_value) / float(r.pass1_undersell_due_to_stcg_cap)
            if r.pass1_undersell_due_to_stcg_cap > 0
            else 0.0
        )
    )

    headroom: Optional[Decimal] = extra_headroom
    for r in candidates:
        if headroom is None or headroom <= 0:
            break
        result = _sell_from_row(
            r,
            r.pass1_undersell_due_to_stcg_cap,
            headroom,
            lt_available=r.lt_value_inr - r.pass1_sell_lt_amount,
            st_available=r.st_value_inr - r.pass1_sell_st_amount,
        )
        extra = result["sold_lt"] + result["sold_st"]
        sold_pass2[r.isin] = extra
        headroom = result["stcg_remaining"]

    out: list[FundRowAfterStep5] = []
    for r in rows:
        if realised_stcg_p1 > 0 and r.pass1_realised_stcg > 0:
            row_net_off = stcg_net_off_total * r.pass1_realised_stcg / realised_stcg_p1
        else:
            row_net_off = Decimal(0)

        sold_2 = sold_pass2[r.isin]
        undersold_2 = max(r.pass1_undersell_due_to_stcg_cap - sold_2, Decimal(0))
        allocation_6 = r.holding_after_initial_trades - sold_2

        out.append(
            FundRowAfterStep5(
                **r.model_dump(),
                stcg_offset_amount=row_net_off,
                pass2_sell_amount=sold_2,
                pass2_undersell_amount=undersold_2,
                final_holding_amount=allocation_6,
            )
        )

    return out
