"""Chart builder — Current / Target / Plan per SEBI sub_category.

Best for: 'how off am I?' / 'what's the gap?' rebalancing questions.
Bucketing logic mirrors the original ``ai_bridge/rebalancing/charts.py``
``compute_category_gap_bar`` (still on disk; archived in Plan 2 Task 9).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.category_gap_bar.schema import (
    CategoryGapBar,
    NamedSeries,
)

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
    RebalancingComputeResponse,
)


@dataclass
class _Bucket:
    asset_subgroup: str
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


def _bucketise(response: "RebalancingComputeResponse") -> list[_Bucket]:
    by_key: dict[tuple[str, str], _Bucket] = {}
    for s in response.subgroups:
        for row in s.actions:
            buy = row.pass1_buy_amount or Decimal(0)
            sell = (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))
            if row.present_allocation_inr <= 0 and buy <= 0 and sell <= 0:
                continue
            key = (s.asset_subgroup, row.sub_category)
            bucket = by_key.get(key)
            if bucket is None:
                bucket = _Bucket(
                    asset_subgroup=s.asset_subgroup,
                    sub_category=row.sub_category,
                    actions=[],
                )
                by_key[key] = bucket
            bucket.actions.append(row)
    return list(by_key.values())


def _bucket_target(
    bucket: _Bucket,
    all_buckets: list[_Bucket],
    response: "RebalancingComputeResponse",
) -> Decimal:
    parent = next(
        (s for s in response.subgroups if s.asset_subgroup == bucket.asset_subgroup),
        None,
    )
    if parent is None:
        return Decimal(0)
    siblings = [b for b in all_buckets if b.asset_subgroup == bucket.asset_subgroup]
    if len(siblings) <= 1:
        return parent.goal_target_inr
    total_planned = sum((b.planned_final for b in siblings), Decimal(0))
    if total_planned > 0:
        return parent.goal_target_inr * (bucket.planned_final / total_planned)
    return parent.goal_target_inr / len(siblings)


def _f(amount: Decimal) -> float:
    return float(amount)


async def build_category_gap_bar(response: Any) -> CategoryGapBar | None:
    """Build the Current/Target/Plan grouped-bar payload, or None if no actions."""
    buckets = _bucketise(response)
    if not buckets:
        return None

    rows = []
    for b in buckets:
        target = _bucket_target(b, buckets, response)
        gap = abs(target - b.current)
        rows.append((b, target, gap))
    rows.sort(key=lambda x: -x[2])

    return CategoryGapBar(
        title="Where you are vs. where you should be",
        subtitle="Current holdings, target allocation, and the post-rebalance plan",
        caption=None,
        categories=[b.sub_category for b, _, _ in rows],
        series=[
            NamedSeries(name="Current", values=[_f(b.current) for b, _, _ in rows]),
            NamedSeries(name="Target", values=[_f(t) for _, t, _ in rows]),
            NamedSeries(name="Plan", values=[_f(b.planned_final) for b, _, _ in rows]),
        ],
    )
