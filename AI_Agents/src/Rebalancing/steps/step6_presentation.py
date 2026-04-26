"""Step 6 — assemble the response.

No spreadsheet column. Aggregates per-fund totals, assembles the trade
list with customer-facing rationale strings, and builds a per-subgroup
summary (target vs current vs final holding plus action rows). Both
the full audit trail (`rows`) and the presentation view (`subgroups`)
ship in the response.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from ..config import (
    ENGINE_VERSION,
    EXIT_FLOOR_RATING,
    LTCG_ANNUAL_EXEMPTION_INR,
    LTCG_RATE_EQUITY_PCT,
    MULTI_FUND_CAP_PCT,
    OTHERS_FUND_CAP_PCT,
    REBALANCE_MIN_CHANGE_PCT,
    ST_THRESHOLD_MONTHS_DEBT,
    ST_THRESHOLD_MONTHS_EQUITY,
    STCG_RATE_EQUITY_PCT,
)
from ..models import (
    FundRowAfterStep5,
    KnobSnapshot,
    RebalancingComputeRequest,
    RebalancingComputeResponse,
    RebalancingRunMetadata,
    RebalancingTotals,
    RebalancingWarning,
    SubgroupSummary,
    TradeAction,
)
from ..rationales import get_rationale
from ..tables import MULTI_CAP_SUB_CATEGORIES
from ..utils import estimate_tax


def _build_knob_snapshot() -> KnobSnapshot:
    return KnobSnapshot(
        multi_fund_cap_pct=MULTI_FUND_CAP_PCT,
        others_fund_cap_pct=OTHERS_FUND_CAP_PCT,
        rebalance_min_change_pct=REBALANCE_MIN_CHANGE_PCT,
        exit_floor_rating=EXIT_FLOOR_RATING,
        ltcg_annual_exemption_inr=LTCG_ANNUAL_EXEMPTION_INR,
        stcg_rate_equity_pct=STCG_RATE_EQUITY_PCT,
        ltcg_rate_equity_pct=LTCG_RATE_EQUITY_PCT,
        st_threshold_months_equity=ST_THRESHOLD_MONTHS_EQUITY,
        st_threshold_months_debt=ST_THRESHOLD_MONTHS_DEBT,
        multi_cap_sub_categories=sorted(MULTI_CAP_SUB_CATEGORIES),
    )


def _row_has_action(r: FundRowAfterStep5) -> bool:
    return r.pass1_buy_amount > 0 or (r.pass1_sell_amount + r.pass2_sell_amount) > 0


def _trade_action_for(r: FundRowAfterStep5) -> TradeAction | None:
    sold = r.pass1_sell_amount + r.pass2_sell_amount
    bought = r.pass1_buy_amount
    if sold > 0:
        if not r.is_recommended:
            action, reason = "EXIT", "exit_bad_fund"
        elif r.fund_rating < EXIT_FLOOR_RATING:
            action, reason = "EXIT", "exit_low_rated"
        else:
            action, reason = "SELL", "trim_over_target"
        amt = sold
    elif bought > 0:
        action = "BUY"
        reason = "cap_spill_buy" if r.rank > 1 else "add_to_target"
        amt = bought
    else:
        return None
    title, text = get_rationale(reason)
    return TradeAction(
        isin=r.isin,
        asset_subgroup=r.asset_subgroup,
        sub_category=r.sub_category,
        recommended_fund=r.recommended_fund,
        action=action,
        amount_inr=amt,
        reason_code=reason,
        reason_title=title,
        reason_text=text,
    )


def _build_subgroups(rows: list[FundRowAfterStep5]) -> list[SubgroupSummary]:
    """Group rows by `asset_subgroup` and compute per-subgroup totals.
    Subgroups with neither goal allocation nor any holding are dropped
    (they're rank-table noise — defined but unused for this client).
    Order: by goal_target_inr descending, then by current_holding_inr
    descending — biggest allocations first."""
    by_sg: dict[str, list[FundRowAfterStep5]] = defaultdict(list)
    for r in rows:
        by_sg[r.asset_subgroup].append(r)

    out: list[SubgroupSummary] = []
    for sg, sg_rows in by_sg.items():
        goal_target = sum((r.target_amount_pre_cap for r in sg_rows), Decimal(0))
        current = sum((r.present_allocation_inr for r in sg_rows), Decimal(0))
        if goal_target == 0 and current == 0:
            continue
        suggested_final = sum(
            (r.final_holding_amount for r in sg_rows), Decimal(0)
        )
        total_buy = sum((r.pass1_buy_amount for r in sg_rows), Decimal(0))
        total_sell = sum(
            (r.pass1_sell_amount + r.pass2_sell_amount for r in sg_rows),
            Decimal(0),
        )
        actions = [
            r for r in sg_rows
            if r.final_target_amount > 0 or r.present_allocation_inr > 0
        ]
        ranks_with_action = sum(1 for r in sg_rows if _row_has_action(r))

        out.append(SubgroupSummary(
            asset_subgroup=sg,
            goal_target_inr=goal_target,
            current_holding_inr=current,
            suggested_final_holding_inr=suggested_final,
            rebalance_inr=suggested_final - current,
            total_buy_inr=total_buy,
            total_sell_inr=total_sell,
            ranks_total=len(sg_rows),
            ranks_with_holding=sum(
                1 for r in sg_rows if r.present_allocation_inr > 0
            ),
            ranks_with_action=ranks_with_action,
            actions=actions,
        ))

    out.sort(key=lambda s: (-float(s.goal_target_inr), -float(s.current_holding_inr)))
    return out


def _exit_load_realised_total(rows: list[FundRowAfterStep5]) -> Decimal:
    """Approximate realised exit load. Assumes in-period units are sold
    LAST within a fund (per the LT → ST OOL → ST IL priority), so
    the load only kicks in when the sold amount exceeds the
    out-of-period portion of the holding."""
    total = Decimal(0)
    for r in rows:
        if r.exit_load_pct <= 0:
            continue
        in_period_value = r.units_within_exit_load_period * r.current_nav
        if in_period_value <= 0:
            continue
        sold = r.pass1_sell_amount + r.pass2_sell_amount
        out_of_period = max(r.present_allocation_inr - in_period_value, Decimal(0))
        from_in_period = max(sold - out_of_period, Decimal(0))
        from_in_period = min(from_in_period, in_period_value)
        total += from_in_period * Decimal(str(r.exit_load_pct)) / Decimal(100)
    return total


def apply(
    rows: list[FundRowAfterStep5],
    request: RebalancingComputeRequest,
    warnings: list[RebalancingWarning],
    unrebalanced_remainder_inr: Decimal,
) -> RebalancingComputeResponse:
    total_buy = sum((r.pass1_buy_amount for r in rows), Decimal(0))
    total_sell = sum((r.pass1_sell_amount + r.pass2_sell_amount for r in rows), Decimal(0))
    total_stcg = sum((r.pass1_realised_stcg for r in rows), Decimal(0))
    total_ltcg = sum((r.pass1_realised_ltcg for r in rows), Decimal(0))
    total_stcg_net_off = sum((r.stcg_offset_amount for r in rows), Decimal(0))
    total_exit_load = _exit_load_realised_total(rows)

    total_tax = estimate_tax(
        total_stcg - total_stcg_net_off,
        total_ltcg,
        request.tax_regime,
        STCG_RATE_EQUITY_PCT,
        LTCG_RATE_EQUITY_PCT,
        LTCG_ANNUAL_EXEMPTION_INR,
    )

    funds_to_buy = sum(1 for r in rows if r.pass1_buy_amount > 0)
    funds_to_sell = sum(
        1 for r in rows
        if (r.pass1_sell_amount + r.pass2_sell_amount) > 0
        and r.is_recommended
        and r.fund_rating >= EXIT_FLOOR_RATING
    )
    funds_to_exit = sum(1 for r in rows if r.exit_flag and r.present_allocation_inr > 0)
    funds_held = sum(
        1 for r in rows
        if not r.worth_to_change and r.present_allocation_inr > 0
    )

    totals = RebalancingTotals(
        total_buy_inr=total_buy,
        total_sell_inr=total_sell,
        net_cash_flow_inr=total_buy - total_sell,
        total_stcg_realised=total_stcg,
        total_ltcg_realised=total_ltcg,
        total_stcg_net_off=total_stcg_net_off,
        total_tax_estimate_inr=total_tax,
        total_exit_load_inr=total_exit_load,
        unrebalanced_remainder_inr=unrebalanced_remainder_inr,
        rows_count=len(rows),
        funds_to_buy_count=funds_to_buy,
        funds_to_sell_count=funds_to_sell,
        funds_to_exit_count=funds_to_exit,
        funds_held_count=funds_held,
    )

    metadata = RebalancingRunMetadata(
        computed_at=datetime.now(timezone.utc),
        engine_version=ENGINE_VERSION,
        request_corpus_inr=request.total_corpus,
        knob_snapshot=_build_knob_snapshot(),
        request_id=request.request_id,
    )

    trade_list: list[TradeAction] = []
    for r in rows:
        ta = _trade_action_for(r)
        if ta:
            trade_list.append(ta)

    return RebalancingComputeResponse(
        rows=rows,
        subgroups=_build_subgroups(rows),
        totals=totals,
        metadata=metadata,
        trade_list=trade_list,
        warnings=warnings,
    )
