"""Step 3 — tax classification for sell candidates.

Spreadsheet refs: cols S (`exit_load_amount`), T (`stcg_amount`),
U (`st_investment_value`), V (`ltcg_amount`), W (`lt_investment_value`).

For each row that is a sell candidate (`exit_flag` OR
(`worth_to_change` AND `diff < 0`)), compute:
  - `stcg_amount = st_value − st_cost` (signed; negative = realised loss)
  - `ltcg_amount = lt_value − lt_cost`
  - `exit_load_amount` — *potential* load if all in-period units are sold.
    Step 4 prefers selling out-of-period units first, so the actual load
    paid is generally lower than this potential value.

Rows that aren't sell candidates carry zeros — they don't realise gains.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import (
    FundRowAfterStep2,
    FundRowAfterStep3,
    RebalancingComputeRequest,
)
from ..utils import compute_exit_load, compute_ltcg, compute_stcg


def apply(
    rows: list[FundRowAfterStep2],
    request: RebalancingComputeRequest,
) -> list[FundRowAfterStep3]:
    _ = request
    out: list[FundRowAfterStep3] = []

    for r in rows:
        is_sell = r.exit_flag or (r.worth_to_change and r.diff < 0)
        if is_sell:
            stcg = compute_stcg(r.st_value_inr, r.st_cost_inr)
            ltcg = compute_ltcg(r.lt_value_inr, r.lt_cost_inr)
            in_period_value = r.units_within_exit_load_period * r.current_nav
            exit_load = compute_exit_load(in_period_value, r.exit_load_pct)
        else:
            stcg = Decimal(0)
            ltcg = Decimal(0)
            exit_load = Decimal(0)

        out.append(
            FundRowAfterStep3(
                **r.model_dump(),
                stcg_amount=stcg,
                ltcg_amount=ltcg,
                exit_load_amount=exit_load,
            )
        )

    return out
