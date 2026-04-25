"""Bridge: GoalAllocationOutput + synthetic holdings + ranking CSV
→ RebalancingComputeRequest.

This is the dev-only precursor to the production
`app/services/ai_bridge/rebalancing_input_builder.py` (next iteration).
Same job (materialise a `RebalancingComputeRequest`), but reads static
fixtures + the CSV ranking instead of the database.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from goal_based_allocation_pydantic import AllocationInput, GoalAllocationOutput

from Rebalancing.models import FundRowInput, RebalancingComputeRequest

from .nav_cache import get_nav
from .profiles import HoldingRecord


# Synthetic LT/ST split + exit-load shape per holding.
LT_SHARE = Decimal("0.60")
LT_COST_RATIO = Decimal("0.85")          # 15% LTCG ratio
ST_COST_RATIO = Decimal("0.95")          # 5% STCG ratio
ST_IN_LOAD_SHARE = Decimal("0.10")       # 10% of ST inside exit-load period
DEFAULT_EXIT_LOAD_PCT = 1.0
DEFAULT_EXIT_LOAD_MONTHS = 12


def load_ranking(csv_path: Path) -> dict[str, list[dict]]:
    """Load the ranking CSV → `{asset_subgroup: [rank_row, ...]}` sorted by rank."""
    by_sg: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            by_sg[row["asset_subgroup"]].append({
                "rank": int(row["rank"]),
                "isin": row["isin"],
                "sub_category": row["sub_category"],
                "fund_name": row["recommended_fund"],
            })
    for sg in by_sg:
        by_sg[sg].sort(key=lambda r: r["rank"])
    return by_sg


def rank1_lookup(ranking: dict[str, list[dict]]) -> dict[str, dict]:
    """Project to `{asset_subgroup: rank1_row}`."""
    out: dict[str, dict] = {}
    for sg, rows in ranking.items():
        if rows:
            out[sg] = rows[0]
    return out


def _holding_breakdown(present: Decimal, nav: Decimal) -> dict[str, Decimal]:
    if present <= 0:
        return {
            "lt_value": Decimal(0), "lt_cost": Decimal(0),
            "st_value": Decimal(0), "st_cost": Decimal(0),
            "units_in_load": Decimal(0),
        }
    lt_value = (present * LT_SHARE).quantize(Decimal("1"))
    st_value = present - lt_value
    lt_cost = (lt_value * LT_COST_RATIO).quantize(Decimal("1"))
    st_cost = (st_value * ST_COST_RATIO).quantize(Decimal("1"))
    in_load_value = (st_value * ST_IN_LOAD_SHARE).quantize(Decimal("1"))
    units_in_load = (in_load_value / nav).quantize(Decimal("0.0001"))
    return {
        "lt_value": lt_value, "lt_cost": lt_cost,
        "st_value": st_value, "st_cost": st_cost,
        "units_in_load": units_in_load,
    }


def build_request(
    profile: AllocationInput,
    allocation_output: GoalAllocationOutput,
    holdings: list[HoldingRecord],
    ranking: dict[str, list[dict]],
) -> RebalancingComputeRequest:
    held_by_isin = {h.isin: h for h in holdings}

    target_by_subgroup: dict[str, Decimal] = {
        r.subgroup: Decimal(str(r.total))
        for r in allocation_output.aggregated_subgroups
    }

    rows: list[FundRowInput] = []
    seen: set[str] = set()

    for subgroup, ranks in ranking.items():
        rank1_target = target_by_subgroup.get(subgroup, Decimal(0))
        for r in ranks:
            target_amount = rank1_target if r["rank"] == 1 else Decimal(0)
            nav = get_nav(r["isin"])

            held = held_by_isin.get(r["isin"])
            if held is not None:
                bd = _holding_breakdown(held.present_inr, nav)
                present = held.present_inr
                fund_rating = held.fund_rating
            else:
                bd = _holding_breakdown(Decimal(0), nav)
                present = Decimal(0)
                fund_rating = 8

            rows.append(FundRowInput(
                asset_subgroup=subgroup,
                sub_category=r["sub_category"],
                recommended_fund=r["fund_name"],
                isin=r["isin"],
                rank=r["rank"],
                target_amount_pre_cap=target_amount,
                present_allocation_inr=present,
                invested_cost_inr=bd["lt_cost"] + bd["st_cost"],
                lt_value_inr=bd["lt_value"],
                lt_cost_inr=bd["lt_cost"],
                st_value_inr=bd["st_value"],
                st_cost_inr=bd["st_cost"],
                units_within_exit_load_period=bd["units_in_load"],
                exit_load_pct=DEFAULT_EXIT_LOAD_PCT,
                exit_load_months=DEFAULT_EXIT_LOAD_MONTHS,
                current_nav=nav,
                fund_rating=fund_rating,
                is_recommended=True,
            ))
            seen.add(r["isin"])

    # BAD-fund rows: held ISINs not in any rank list.
    for h in holdings:
        if h.isin in seen:
            continue
        nav = get_nav(h.isin)
        bd = _holding_breakdown(h.present_inr, nav)
        rows.append(FundRowInput(
            asset_subgroup=h.asset_subgroup,
            sub_category=h.sub_category,
            recommended_fund=h.fund_name,
            isin=h.isin,
            rank=0,
            target_amount_pre_cap=Decimal(0),
            present_allocation_inr=h.present_inr,
            invested_cost_inr=bd["lt_cost"] + bd["st_cost"],
            lt_value_inr=bd["lt_value"],
            lt_cost_inr=bd["lt_cost"],
            st_value_inr=bd["st_value"],
            st_cost_inr=bd["st_cost"],
            units_within_exit_load_period=bd["units_in_load"],
            exit_load_pct=DEFAULT_EXIT_LOAD_PCT,
            exit_load_months=DEFAULT_EXIT_LOAD_MONTHS,
            current_nav=nav,
            fund_rating=h.fund_rating,
            is_recommended=False,
        ))

    return RebalancingComputeRequest(
        total_corpus=Decimal(str(profile.total_corpus)),
        tax_regime=profile.tax_regime,
        effective_tax_rate_pct=profile.effective_tax_rate,
        rounding_step=100,
        rows=rows,
    )
