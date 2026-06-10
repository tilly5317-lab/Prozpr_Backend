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
from ..rationales import STCG_CAP_SUFFIX_TEMPLATE, get_rationale
from ..tables import SUBGROUP_FUND_CAP_PCT
from ..utils import estimate_tax

# Cross-agent type — same documented exception as in pipeline.py / models.py.
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
    PracticalAllocationOutput,
)


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
        # List of subgroups with a non-default per-fund cap (sorted for stable
        # output). Includes multi_asset (20%) and short_debt (30%).
        multi_fund_cap_subgroups=sorted(SUBGROUP_FUND_CAP_PCT.keys()),
    )


def _frozen_subgroups(practical: PracticalAllocationOutput) -> list[SubgroupSummary]:
    """Two frozen entries for non-MF exposures the engine doesn't trade
    per-fund. Sourced from practical.corpus_breakdown."""
    cb = practical.corpus_breakdown
    elss = Decimal(str(cb.elss_corpus_inr))
    nme_input = Decimal(str(cb.non_mf_equity_input_inr))
    nme_actual = Decimal(str(cb.non_mf_equity_actual_inr))

    out: list[SubgroupSummary] = []
    if elss > 0:
        out.append(SubgroupSummary(
            asset_subgroup="tax_efficient_equities",
            goal_target_inr=elss,
            current_holding_inr=elss,
            suggested_final_holding_inr=elss,
            rebalance_inr=Decimal(0),
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            ranks_total=0,
            ranks_with_holding=0,
            ranks_with_action=0,
            actions=[],
        ))
    if nme_input > 0 or nme_actual > 0:
        out.append(SubgroupSummary(
            asset_subgroup="non_mf_equities",
            goal_target_inr=nme_actual,
            current_holding_inr=nme_input,
            suggested_final_holding_inr=nme_actual,
            rebalance_inr=nme_actual - nme_input,
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            ranks_total=0,
            ranks_with_holding=0,
            ranks_with_action=0,
            actions=[],
        ))
    return out


def _sell_direct_stocks_action(
    practical: PracticalAllocationOutput,
) -> TradeAction | None:
    """C.6(b): single SELL_DIRECT_STOCKS trade when the NFA-banded cap has
    trimmed the customer's direct-stock allocation."""
    excess = Decimal(str(practical.corpus_breakdown.excess_direct_stocks_inr))
    if excess <= 0:
        return None
    title, text = get_rationale("sell_excess_direct_stocks")
    # `common.format_inr_indian` is the project standard (see
    # `AI_Agents/src/common.py`); import locally to avoid a top-level dep
    # on the cross-agent helper at module load time.
    from common import format_inr_indian  # type: ignore[import-not-found]

    return TradeAction(
        isin=None,
        asset_subgroup="non_mf_equities",
        sub_category=None,
        recommended_fund=None,
        action="SELL_DIRECT_STOCKS",
        amount_inr=excess,
        reason_code="sell_excess_direct_stocks",
        reason_title=title,
        reason_text=text.replace("{amount}", format_inr_indian(int(excess))),
        fund_reason=None,
    )


def _row_has_action(r: FundRowAfterStep5) -> bool:
    return r.pass1_buy_amount > 0 or (r.pass1_sell_amount + r.pass2_sell_amount) > 0


def _trade_action_for(r: FundRowAfterStep5) -> TradeAction | None:
    sold = r.pass1_sell_amount + r.pass2_sell_amount
    bought = r.pass1_buy_amount
    if sold > 0:
        if r.exit_flag:
            action = "EXIT"
            reason = (
                "exit_low_rated" if r.fund_rating < EXIT_FLOOR_RATING
                else "exit_bad_fund"
            )
        elif r.rank == 0:
            # NEUTRAL row — LT-only migration into the recommended pick.
            action, reason = "SELL", "migrate_neutral_to_recommended"
        else:
            # Trim of a still-recommended fund: show why we rate it, so the
            # customer understands we're only adjusting weight, not dropping it.
            action, reason = "SELL", "trim_over_target"
            fund_reason = r.selection_reason
        amt = sold
        # CSV is the source of truth for fund-level rationale on every
        # trade. For force-exit / NEUTRAL migrations, the CSV carries
        # `rejection_reason` (negative framing). For recommended-fund
        # trims and low-rated exits of recommended funds, only
        # `selection_reason` exists — and that's fine because the action
        # text for those codes already establishes "fund is good, this
        # is an allocation move", so the positive CSV text reinforces
        # rather than contradicts.
        fund_reason = r.rejection_reason or r.selection_reason
    elif bought > 0:
        action = "BUY"
        reason = "cap_spill_buy" if r.rank > 1 else "add_to_target"
        amt = bought
        fund_reason = r.selection_reason or r.rejection_reason
    else:
        return None
    title, text = get_rationale(reason)
    # When the STCG brake forced a partial sell, append a one-sentence note
    # so the customer (and the LLM narrating this) understands *why* the
    # action is smaller than the full demand. Append-only — reason_code is
    # unchanged so downstream branching on it still works.
    if sold > 0 and r.pass1_undersell_due_to_stcg_cap > 0:
        from common import format_inr_indian  # type: ignore[import-not-found]
        text = text + STCG_CAP_SUFFIX_TEMPLATE.replace(
            "{amount}", format_inr_indian(int(r.pass1_undersell_due_to_stcg_cap))
        )
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
        fund_reason=fund_reason,
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


def apply(
    rows: list[FundRowAfterStep5],
    request: RebalancingComputeRequest,
    warnings: list[RebalancingWarning],
    unrebalanced_remainder_inr: Decimal,
    practical: PracticalAllocationOutput,
) -> RebalancingComputeResponse:
    total_buy = sum((r.pass1_buy_amount for r in rows), Decimal(0))
    total_sell = sum((r.pass1_sell_amount + r.pass2_sell_amount for r in rows), Decimal(0))
    total_stcg = sum((r.pass1_realised_stcg for r in rows), Decimal(0))
    total_ltcg = sum((r.pass1_realised_ltcg for r in rows), Decimal(0))
    total_stcg_net_off = sum((r.stcg_offset_amount for r in rows), Decimal(0))

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
        and not r.exit_flag
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
        request_corpus_inr=Decimal(str(request.practical_allocation_input.total_corpus)),
        knob_snapshot=_build_knob_snapshot(),
        request_id=request.request_id,
    )

    trade_list: list[TradeAction] = []
    for r in rows:
        ta = _trade_action_for(r)
        if ta:
            trade_list.append(ta)
    sds = _sell_direct_stocks_action(practical)
    if sds is not None:
        trade_list.append(sds)

    subgroups = _build_subgroups(rows) + _frozen_subgroups(practical)
    # Preserve the biggest-first sort across MF + frozen entries.
    subgroups.sort(
        key=lambda s: (-float(s.goal_target_inr), -float(s.current_holding_inr))
    )

    return RebalancingComputeResponse(
        rows=rows,
        subgroups=subgroups,
        totals=totals,
        metadata=metadata,
        trade_list=trade_list,
        warnings=warnings,
        practical_allocation=practical,
    )
