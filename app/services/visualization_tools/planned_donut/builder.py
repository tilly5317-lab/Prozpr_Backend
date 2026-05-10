"""Chart builder — share of planned-final allocation by SEBI sub-category.

Best for: 'what does my portfolio look like after rebalancing?' questions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.planned_donut.schema import (
    PlannedDonut,
    PlannedDonutSlice,
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
    def current(self) -> Decimal:
        return sum((r.present_allocation_inr for r in self.actions), Decimal(0))

    @property
    def buy_total(self) -> Decimal:
        return sum(((r.pass1_buy_amount or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def sell_total(self) -> Decimal:
        return sum(
            (((r.pass1_sell_amount or Decimal(0)) + (r.pass2_sell_amount or Decimal(0)))
             for r in self.actions),
            Decimal(0),
        )

    @property
    def planned_final(self) -> Decimal:
        return self.current - self.sell_total + self.buy_total


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


async def build_planned_donut(response: Any) -> PlannedDonut | None:
    """Build the post-rebalance allocation donut payload, or None if no actions."""
    buckets = _bucketise(response)
    slices = [
        PlannedDonutSlice(label=b.sub_category, value=float(b.planned_final))
        for b in buckets
        if b.planned_final > 0
    ]
    if not slices:
        return None
    slices.sort(key=lambda s: -s.value)

    return PlannedDonut(
        title="Your portfolio after rebalancing",
        subtitle="Share of corpus by category in the planned allocation",
        caption=None,
        slices=slices,
    )
