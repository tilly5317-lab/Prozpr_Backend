"""Chart builder — exit-load + realised gains (ST/LT) per SEBI sub-category.

Best for: 'what does this rebalance cost me?' questions. Skipped when totals
are all zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.tax_cost_bar.schema import (
    TaxCostBar,
    TaxCostNamedSeries,
    TaxCostTotals,
)

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
)


@dataclass
class _Bucket:
    sub_category: str
    actions: list["FundRowAfterStep5"]

    @property
    def realised_stcg(self) -> Decimal:
        return sum(((r.pass1_realised_stcg or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def realised_ltcg(self) -> Decimal:
        return sum(((r.pass1_realised_ltcg or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def exit_load_inr(self) -> Decimal:
        # exit_load_amount is the *potential* load if all in-period units sold.
        # Apportion by fraction actually sold from this row.
        total = Decimal(0)
        for r in self.actions:
            sold = (r.pass1_sell_amount or Decimal(0)) + (r.pass2_sell_amount or Decimal(0))
            present = r.present_allocation_inr
            potential = r.exit_load_amount or Decimal(0)
            if present > 0 and sold > 0:
                total += potential * (sold / present)
        return total


def _bucketise(response: Any) -> list[_Bucket]:
    by_key: dict[str, _Bucket] = {}
    for s in response.subgroups:
        for row in s.actions:
            buy = row.pass1_buy_amount or Decimal(0)
            sell = (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))
            if row.present_allocation_inr <= 0 and buy <= 0 and sell <= 0:
                continue
            bucket = by_key.get(row.sub_category)
            if bucket is None:
                bucket = _Bucket(sub_category=row.sub_category, actions=[])
                by_key[row.sub_category] = bucket
            bucket.actions.append(row)
    return list(by_key.values())


def _f(amount: Decimal) -> float:
    return float(amount)


async def build_tax_cost_bar(response: Any) -> TaxCostBar | None:
    """Build the per-category cost stacked-bar payload, or None if no taxes."""
    totals = response.totals
    if (
        (totals.total_tax_estimate_inr or 0) <= 0
        and (totals.total_exit_load_inr or 0) <= 0
    ):
        return None

    buckets = _bucketise(response)
    rows = [
        b for b in buckets
        if b.exit_load_inr > 0 or b.realised_stcg > 0 or b.realised_ltcg > 0
    ]
    if not rows:
        return None
    rows.sort(key=lambda b: -(b.exit_load_inr + b.realised_stcg + b.realised_ltcg))

    return TaxCostBar(
        title="Cost of rebalancing per category",
        subtitle="Realised short-term and long-term gains plus exit loads",
        caption=None,
        categories=[b.sub_category for b in rows],
        series=[
            TaxCostNamedSeries(name="Short-term gains", values=[_f(b.realised_stcg) for b in rows]),
            TaxCostNamedSeries(name="Long-term gains", values=[_f(b.realised_ltcg) for b in rows]),
            TaxCostNamedSeries(name="Exit load", values=[_f(b.exit_load_inr) for b in rows]),
        ],
        totals=TaxCostTotals(
            tax_estimate_inr=_f(totals.total_tax_estimate_inr or Decimal(0)),
            exit_load_inr=_f(totals.total_exit_load_inr or Decimal(0)),
        ),
    )
