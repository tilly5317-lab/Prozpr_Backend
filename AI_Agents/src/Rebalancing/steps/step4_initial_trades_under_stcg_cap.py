"""Step 4 — initial rebalance pass under the STCG offset budget.

Spreadsheet refs: cols X–AC (pass-1 buys/sells, `holding_after_initial_trades`)
and AD–AL (LT/ST split, carryforward, w/o-ST counterfactual).

Algorithm
---------
1. Classify rows into buy candidates, forced sells (BAD or low-rated),
   and optional sells (over-allocated recommended funds).
2. Sort sells tax-cheap first within each bucket. Forced sells run
   regardless of buy demand; optional sells fill any gap up to the
   total buy demand (closed-system constraint, Decision 9).
3. Within each fund, walk LT → ST out-of-load → ST in-load. Cap ST
   gains by the remaining STCG offset budget; record the unsold ST
   value and its STCG cost on `pass1_undersell_due_to_stcg_cap*`.
4. Distribute buys; if total sells < total buy demand, scale buys
   proportionally and record `pass1_underbuy_amount`.
5. Re-run forced+optional with budget=∞ to capture
   `pass1_sell_amount_no_stcg_cap` (counterfactual, cols AI–AK).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..config import LTCG_RATE_EQUITY_PCT, STCG_RATE_EQUITY_PCT
from ..models import (
    FundRowAfterStep3,
    FundRowAfterStep4,
    RebalancingComputeRequest,
    RebalancingWarning,
    WarningCode,
)
from ..utils import floor_to_step


def _tax_cheapness_key(row: FundRowAfterStep3) -> float:
    """Lower = cheaper. Average tax + exit-load cost per rupee sold from
    this fund. Used to sort sell candidates within a bucket."""
    total = float(row.present_allocation_inr)
    if total <= 0:
        return 0.0

    lt_share = float(row.lt_value_inr) / total
    st_share = float(row.st_value_inr) / total
    in_period_value = float(row.units_within_exit_load_period * row.current_nav)
    il_share = in_period_value / total if total > 0 else 0.0

    lt_gain_ratio = (
        float(row.ltcg_amount) / float(row.lt_value_inr)
        if row.lt_value_inr > 0 else 0.0
    )
    lt_eff = max(lt_gain_ratio, 0.0) * LTCG_RATE_EQUITY_PCT / 100.0

    st_gain_ratio = (
        float(row.stcg_amount) / float(row.st_value_inr)
        if row.st_value_inr > 0 else 0.0
    )
    # Negative ratio (loss) → negative effective rate (cheaper).
    st_eff = st_gain_ratio * STCG_RATE_EQUITY_PCT / 100.0

    il_eff = il_share * row.exit_load_pct / 100.0

    return lt_share * lt_eff + st_share * st_eff + il_eff


def _sell_from_row(
    row: FundRowAfterStep3,
    amount: Decimal,
    stcg_remaining: Optional[Decimal],
) -> dict:
    """Sell up to `amount` rupees from `row`, walking LT → ST OOL → ST IL.
    `stcg_remaining` is the per-portfolio STCG budget (None = unlimited).
    Caller is responsible for threading the returned `stcg_remaining` to
    the next call.
    """
    sold_lt = Decimal(0)
    sold_st_ool = Decimal(0)
    sold_st_il = Decimal(0)
    ltcg_realised = Decimal(0)
    stcg_realised = Decimal(0)
    exit_load_realised = Decimal(0)
    undersold = Decimal(0)
    undersold_stcg = Decimal(0)

    remaining = amount

    # 1. LT first (cheapest tax bucket).
    if remaining > 0 and row.lt_value_inr > 0:
        from_lt = min(remaining, row.lt_value_inr)
        sold_lt = from_lt
        ltcg_realised = from_lt * row.ltcg_amount / row.lt_value_inr
        remaining -= from_lt

    in_period_value = row.units_within_exit_load_period * row.current_nav
    st_ool_value = max(row.st_value_inr - in_period_value, Decimal(0))
    st_il_value = min(row.st_value_inr, in_period_value)
    st_gain_ratio = (
        row.stcg_amount / row.st_value_inr
        if row.st_value_inr > 0 else Decimal(0)
    )

    # 2. ST out-of-load.
    if remaining > 0 and st_ool_value > 0:
        from_st_ool = min(remaining, st_ool_value)
        stcg_from_ool = from_st_ool * st_gain_ratio
        from_st_ool, stcg_from_ool, stcg_remaining, u, u_stcg = _apply_stcg_budget(
            from_st_ool, stcg_from_ool, stcg_remaining
        )
        undersold += u
        undersold_stcg += u_stcg
        sold_st_ool = from_st_ool
        stcg_realised += stcg_from_ool
        remaining -= from_st_ool

    # 3. ST in-load (incurs exit load on the slice sold).
    if remaining > 0 and st_il_value > 0:
        from_st_il = min(remaining, st_il_value)
        stcg_from_il = from_st_il * st_gain_ratio
        from_st_il, stcg_from_il, stcg_remaining, u, u_stcg = _apply_stcg_budget(
            from_st_il, stcg_from_il, stcg_remaining
        )
        undersold += u
        undersold_stcg += u_stcg
        sold_st_il = from_st_il
        stcg_realised += stcg_from_il
        if from_st_il > 0:
            exit_load_realised = from_st_il * Decimal(str(row.exit_load_pct)) / Decimal(100)
        remaining -= from_st_il

    # Any leftover demand we couldn't satisfy from this row's buckets.
    undersold += remaining

    return {
        "sold_lt": sold_lt,
        "sold_st_ool": sold_st_ool,
        "sold_st_il": sold_st_il,
        "ltcg": ltcg_realised,
        "stcg": stcg_realised,
        "exit_load": exit_load_realised,
        "undersold": undersold,
        "undersold_stcg": undersold_stcg,
        "stcg_remaining": stcg_remaining,
    }


def _apply_stcg_budget(
    slice_amount: Decimal,
    slice_stcg: Decimal,
    stcg_remaining: Optional[Decimal],
) -> tuple[Decimal, Decimal, Optional[Decimal], Decimal, Decimal]:
    """Apply the STCG budget to a single ST slice. Returns
    (allowed_amount, allowed_stcg, new_stcg_remaining, undersold_amount,
    undersold_stcg)."""
    if stcg_remaining is None or slice_stcg <= 0:
        # No cap, or selling at a loss (no STCG realised) — pass through.
        if stcg_remaining is not None and slice_stcg < 0:
            # A loss frees STCG headroom (we can absorb more positive STCG later).
            stcg_remaining = stcg_remaining - slice_stcg
        return slice_amount, slice_stcg, stcg_remaining, Decimal(0), Decimal(0)

    if stcg_remaining <= 0:
        return Decimal(0), Decimal(0), stcg_remaining, slice_amount, slice_stcg

    if slice_stcg <= stcg_remaining:
        return slice_amount, slice_stcg, stcg_remaining - slice_stcg, Decimal(0), Decimal(0)

    # Partial fit: scale slice to fit remaining headroom.
    affordable_amount = stcg_remaining * slice_amount / slice_stcg
    return (
        affordable_amount,
        stcg_remaining,
        Decimal(0),
        slice_amount - affordable_amount,
        slice_stcg - stcg_remaining,
    )


def _empty_row_state() -> dict:
    return {
        "pass1_buy_amount": Decimal(0),
        "pass1_underbuy_amount": Decimal(0),
        "pass1_sell_amount": Decimal(0),
        "pass1_undersell_amount": Decimal(0),
        "pass1_sell_lt_amount": Decimal(0),
        "pass1_realised_ltcg": Decimal(0),
        "pass1_sell_st_amount": Decimal(0),
        "pass1_realised_stcg": Decimal(0),
        "pass1_blocked_stcg_value": Decimal(0),
    }


def _execute_sells(
    candidates: list[FundRowAfterStep3],
    state: dict[str, dict],
    is_forced: bool,
    remaining_buy_demand: Decimal,
    stcg_remaining: Optional[Decimal],
) -> tuple[Decimal, Optional[Decimal]]:
    """Returns (remaining_buy_demand_after, stcg_remaining_after)."""
    for r in candidates:
        if is_forced:
            demand = r.present_allocation_inr
        else:
            if remaining_buy_demand <= 0:
                break
            excess = -r.diff
            demand = min(excess, r.present_allocation_inr, remaining_buy_demand)
        if demand <= 0:
            continue

        result = _sell_from_row(r, demand, stcg_remaining)
        stcg_remaining = result["stcg_remaining"]

        sold_lt = result["sold_lt"]
        sold_st = result["sold_st_ool"] + result["sold_st_il"]
        sold = sold_lt + sold_st

        s = state[r.isin]
        s["pass1_sell_amount"] += sold
        s["pass1_sell_lt_amount"] += sold_lt
        s["pass1_realised_ltcg"] += result["ltcg"]
        s["pass1_sell_st_amount"] += sold_st
        s["pass1_realised_stcg"] += result["stcg"]
        s["pass1_undersell_amount"] += result["undersold"]
        s["pass1_blocked_stcg_value"] += result["undersold_stcg"]

        if not is_forced:
            remaining_buy_demand -= sold

    return remaining_buy_demand, stcg_remaining


def _counterfactual_sold_per_row(
    forced_sorted: list[FundRowAfterStep3],
    optional_sorted: list[FundRowAfterStep3],
    target_buy: Decimal,
) -> dict[str, Decimal]:
    """Re-run forced+optional with stcg_budget=∞ to capture
    `pass1_sell_amount_no_stcg_cap`. Cheap because `_sell_from_row` is
    pure."""
    sold_per_row: dict[str, Decimal] = {}
    cumulative = Decimal(0)

    for r in forced_sorted:
        result = _sell_from_row(r, r.present_allocation_inr, None)
        sold = result["sold_lt"] + result["sold_st_ool"] + result["sold_st_il"]
        sold_per_row[r.isin] = sold_per_row.get(r.isin, Decimal(0)) + sold
        cumulative += sold

    remaining_buy = max(target_buy - cumulative, Decimal(0))
    for r in optional_sorted:
        if remaining_buy <= 0:
            break
        excess = -r.diff
        demand = min(excess, r.present_allocation_inr, remaining_buy)
        if demand <= 0:
            continue
        result = _sell_from_row(r, demand, None)
        sold = result["sold_lt"] + result["sold_st_ool"] + result["sold_st_il"]
        sold_per_row[r.isin] = sold_per_row.get(r.isin, Decimal(0)) + sold
        remaining_buy -= sold

    return sold_per_row


def apply(
    rows: list[FundRowAfterStep3],
    request: RebalancingComputeRequest,
) -> tuple[list[FundRowAfterStep4], list[RebalancingWarning]]:
    forced = [r for r in rows if r.exit_flag]
    optional = [r for r in rows if r.worth_to_change and r.diff < 0 and not r.exit_flag]
    buyers = [r for r in rows if r.worth_to_change and r.diff > 0 and r.is_recommended]

    target_buy = sum((r.diff for r in buyers), Decimal(0))

    state: dict[str, dict] = {r.isin: _empty_row_state() for r in rows}

    forced_sorted = sorted(forced, key=_tax_cheapness_key)
    optional_sorted = sorted(
        optional,
        key=lambda r: (_tax_cheapness_key(r), -float(abs(r.diff))),
    )

    stcg_budget = request.stcg_offset_budget_inr
    stcg_remaining: Optional[Decimal] = stcg_budget

    # Forced sells run regardless of buy demand. Their output cash
    # contributes to fulfilling buys; remainder may exceed buy_demand.
    _, stcg_remaining = _execute_sells(
        forced_sorted, state, True, Decimal(0), stcg_remaining
    )

    forced_sold_total = sum(
        (state[r.isin]["pass1_sell_amount"] for r in rows), Decimal(0)
    )
    remaining_buy_demand = max(target_buy - forced_sold_total, Decimal(0))

    _, stcg_remaining = _execute_sells(
        optional_sorted, state, False, remaining_buy_demand, stcg_remaining
    )

    # Distribute buys. Floor each rounded amount so the sum cannot exceed
    # available sell cash (closed-system invariant `Σbuys ≤ Σsells`).
    total_sold_final = sum(
        (state[r.isin]["pass1_sell_amount"] for r in rows), Decimal(0)
    )
    if target_buy > 0:
        scale = (
            Decimal(1)
            if total_sold_final >= target_buy
            else total_sold_final / target_buy
        )
        for r in buyers:
            raw = r.diff * scale
            buy_amt = floor_to_step(raw, request.rounding_step)
            state[r.isin]["pass1_buy_amount"] = buy_amt
            state[r.isin]["pass1_underbuy_amount"] = r.diff - buy_amt

    # Counterfactual: same logic with no STCG budget.
    cf_sold = _counterfactual_sold_per_row(forced_sorted, optional_sorted, target_buy)

    # `stcg_budget_remaining_after_pass1`: leftover STCG headroom. Negative means we
    # over-sold against the budget (only possible if losses freed extra
    # capacity, see `_apply_stcg_budget`).
    if stcg_budget is not None and stcg_remaining is not None:
        balance_cf = stcg_remaining
    else:
        balance_cf = Decimal(0)

    warnings: list[RebalancingWarning] = []
    if stcg_budget is not None:
        binding_isins = [
            r.isin for r in rows
            if state[r.isin]["pass1_blocked_stcg_value"] > 0
        ]
        if binding_isins:
            warnings.append(
                RebalancingWarning(
                    code=WarningCode.STCG_BUDGET_BINDING,
                    message=(
                        "STCG offset budget capped pass-1 sells. "
                        "Carryforward losses may unlock more in pass-2."
                    ),
                    affected_isins=binding_isins,
                )
            )

    out: list[FundRowAfterStep4] = []
    for r in rows:
        s = state[r.isin]
        sold = s["pass1_sell_amount"]
        bought = s["pass1_buy_amount"]
        allocation_5 = r.present_allocation_inr + bought - sold

        out.append(
            FundRowAfterStep4(
                **r.model_dump(),
                pass1_buy_amount=bought,
                pass1_underbuy_amount=s["pass1_underbuy_amount"],
                pass1_sell_amount=sold,
                pass1_undersell_amount=s["pass1_undersell_amount"],
                pass1_sell_lt_amount=s["pass1_sell_lt_amount"],
                pass1_realised_ltcg=s["pass1_realised_ltcg"],
                pass1_sell_st_amount=s["pass1_sell_st_amount"],
                pass1_realised_stcg=s["pass1_realised_stcg"],
                stcg_budget_remaining_after_pass1=balance_cf,
                pass1_sell_amount_no_stcg_cap=cf_sold.get(r.isin, Decimal(0)),
                pass1_undersell_due_to_stcg_cap=s["pass1_undersell_amount"],
                pass1_blocked_stcg_value=s["pass1_blocked_stcg_value"],
                holding_after_initial_trades=allocation_5,
            )
        )

    return out, warnings
