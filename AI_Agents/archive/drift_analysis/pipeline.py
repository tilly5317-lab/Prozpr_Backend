from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from goal_based_allocation_pydantic.tables import FUND_MAPPING

from .models import (
    ActualHolding,
    AssetClassDrift,
    DriftInput,
    DriftOutput,
    FundDrift,
    SubgroupDrift,
)
from .tables import get_display_name


def compute_drift(inp: DriftInput) -> DriftOutput:
    ideal_by_subgroup = _extract_ideal_subgroup_amounts(inp)
    actual_by_subgroup = _group_actuals_by_subgroup(inp.actual_holdings)
    total_ideal = inp.ideal_allocation.grand_total
    total_actual = sum(h.current_value for h in inp.actual_holdings)

    all_subgroups = set(ideal_by_subgroup) | set(actual_by_subgroup)

    subgroup_drifts: list[SubgroupDrift] = []
    for sg in sorted(all_subgroups):
        ideal_amt = ideal_by_subgroup.get(sg, 0.0)
        actual_funds = actual_by_subgroup.get(sg, [])
        actual_sum = sum(f[1] for f in actual_funds)

        if ideal_amt == 0 and actual_sum == 0:
            continue

        fund_map = FUND_MAPPING.get(sg)
        asset_class = fund_map.asset_class if fund_map else "others"
        display_name = get_display_name(sg)

        fund_drifts = _build_fund_drifts(
            sg, ideal_amt, actual_funds, fund_map, asset_class,
            display_name, total_ideal,
        )

        subgroup_drifts.append(SubgroupDrift(
            subgroup=sg,
            display_name=display_name,
            asset_class=asset_class,
            ideal_amount=ideal_amt,
            actual_amount=actual_sum,
            drift_amount=actual_sum - ideal_amt,
            drift_pct=_pct(actual_sum - ideal_amt, total_ideal),
            funds=fund_drifts,
        ))

    asset_classes = _roll_up_asset_classes(subgroup_drifts, total_ideal, total_actual)

    return DriftOutput(
        total_ideal_value=total_ideal,
        total_actual_value=total_actual,
        asset_classes=asset_classes,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────


def _extract_ideal_subgroup_amounts(inp: DriftInput) -> Dict[str, float]:
    return {
        row.subgroup: row.total
        for row in inp.ideal_allocation.aggregated_subgroups
        if row.total > 0
    }


# Each entry in the list is (scheme_code, current_value, scheme_name, isin).
_AggFund = Tuple[str, float, str, str]


def _group_actuals_by_subgroup(
    holdings: List[ActualHolding],
) -> Dict[str, List[_AggFund]]:
    # First aggregate by (subgroup, isin) to merge multi-folio holdings.
    agg: dict[tuple[str, str], tuple[str, float, str]] = {}
    for h in holdings:
        sg = h.asset_subgroup if h.asset_subgroup in FUND_MAPPING else "others"
        key = (sg, h.isin)
        if key in agg:
            prev_code, prev_val, prev_name = agg[key]
            agg[key] = (prev_code, prev_val + h.current_value, prev_name)
        else:
            agg[key] = (h.scheme_code, h.current_value, h.scheme_name)

    grouped: Dict[str, List[_AggFund]] = defaultdict(list)
    for (sg, isin), (code, val, name) in agg.items():
        grouped[sg].append((code, val, name, isin))
    return dict(grouped)


def _build_fund_drifts(
    subgroup: str,
    ideal_amt: float,
    actual_funds: List[_AggFund],
    fund_map,
    asset_class: str,
    display_name: str,
    total_ideal: float,
) -> List[FundDrift]:
    rec_isin = fund_map.isin if fund_map else None
    rec_found = False
    drifts: list[FundDrift] = []

    for code, val, name, isin in actual_funds:
        is_rec = isin == rec_isin
        if is_rec:
            rec_found = True
        ideal = ideal_amt if is_rec else 0.0
        drifts.append(FundDrift(
            scheme_code=code,
            scheme_name=name,
            isin=isin,
            asset_class=asset_class,
            asset_subgroup=subgroup,
            display_name=display_name,
            is_recommended=is_rec,
            ideal_amount=ideal,
            actual_amount=val,
            drift_amount=val - ideal,
            drift_pct=_pct(val - ideal, total_ideal),
        ))

    if not rec_found and fund_map and ideal_amt > 0:
        drifts.append(FundDrift(
            scheme_code=fund_map.asset_subgroup,
            scheme_name=fund_map.recommended_fund,
            isin=fund_map.isin,
            asset_class=asset_class,
            asset_subgroup=subgroup,
            display_name=display_name,
            is_recommended=True,
            ideal_amount=ideal_amt,
            actual_amount=0.0,
            drift_amount=-ideal_amt,
            drift_pct=_pct(-ideal_amt, total_ideal),
        ))

    return drifts


def _roll_up_asset_classes(
    subgroup_drifts: List[SubgroupDrift],
    total_ideal: float,
    total_actual: float,
) -> List[AssetClassDrift]:
    groups: dict[str, list[SubgroupDrift]] = defaultdict(list)
    for sg in subgroup_drifts:
        groups[sg.asset_class].append(sg)

    result: list[AssetClassDrift] = []
    for ac in ("equity", "debt", "others"):
        sgs = groups.get(ac, [])
        if not sgs:
            continue
        ideal_sum = sum(s.ideal_amount for s in sgs)
        actual_sum = sum(s.actual_amount for s in sgs)
        result.append(AssetClassDrift(
            asset_class=ac,
            ideal_amount=ideal_sum,
            ideal_pct=_pct(ideal_sum, total_ideal),
            actual_amount=actual_sum,
            actual_pct=_pct(actual_sum, total_actual),
            drift_amount=actual_sum - ideal_sum,
            drift_pct=_pct(actual_sum - ideal_sum, total_ideal),
            subgroups=sgs,
        ))
    return result


def _pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 2)
