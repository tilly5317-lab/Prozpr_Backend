"""Chart specs and computers for the rebalancing chat reply.

The rebalancing service computes a list of *candidate* chart specs from the
engine response, then a Haiku-driven picker (see ``chart_picker.py``) selects
the most useful one for the user's question. The picked spec is attached to
``RebalancingRunOutcome.chart`` and surfaced to the frontend through the chat
API response so it can render alongside the markdown brief.

Chart specs are deliberately simple JSON-friendly shapes — the frontend owns
the actual visualisation. A spec carries:
  - ``chart_type``: machine label, frontend keys its renderer off this.
  - ``title`` / ``caption``: display copy.
  - ``data``: chart-shaped dict (categories + series, slices, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
    RebalancingComputeResponse,
)


ChartType = Literal[
    "category_gap_bar",
    "planned_donut",
    "tax_cost_bar",
]


class ChartSpec(BaseModel):
    """JSON-friendly chart payload the frontend renders.

    The ``data`` shape varies per ``chart_type``; the frontend keys its
    renderer off ``chart_type`` and reads the corresponding fields out of
    ``data``. See each compute_* function below for the exact shape.
    """

    chart_type: ChartType
    title: str
    caption: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


# ── Bucketing — mirrors formatter._Bucket but kept private to avoid
#    cross-import coupling between formatter and charts.
@dataclass
class _Bucket:
    asset_subgroup: str
    sub_category: str
    actions: list[FundRowAfterStep5]

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


def _bucketise(response: RebalancingComputeResponse) -> list[_Bucket]:
    """Roll engine actions up into one bucket per (asset_subgroup, sub_category)."""
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
    response: RebalancingComputeResponse,
) -> Decimal:
    """Subgroup-target proration matching formatter._bucket_target."""
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
    """Decimal → float for JSON-serialisable chart payloads."""
    return float(amount)


# ── Chart computers ──


def compute_category_gap_bar(response: RebalancingComputeResponse) -> ChartSpec | None:
    """Grouped bar — Current vs Target vs Plan per SEBI sub_category.

    Best for: "how off am I?" / "what's the gap?" questions.
    """
    buckets = _bucketise(response)
    if not buckets:
        return None
    # Order by absolute gap descending so the most off-target bars lead.
    rows = []
    for b in buckets:
        target = _bucket_target(b, buckets, response)
        gap = abs(target - b.current)
        rows.append((b, target, gap))
    rows.sort(key=lambda x: -x[2])

    return ChartSpec(
        chart_type="category_gap_bar",
        title="Where you are vs. where you should be",
        caption="Current holdings, target allocation, and the post-rebalance plan.",
        data={
            "categories": [b.sub_category for b, _, _ in rows],
            "series": [
                {"name": "Current", "values": [_f(b.current) for b, _, _ in rows]},
                {"name": "Target", "values": [_f(t) for _, t, _ in rows]},
                {"name": "Plan", "values": [_f(b.planned_final) for b, _, _ in rows]},
            ],
        },
    )


def compute_planned_donut(response: RebalancingComputeResponse) -> ChartSpec | None:
    """Donut — share of the planned-final allocation by SEBI sub_category.

    Best for: "what does my portfolio look like after this?" questions.
    Drops zero-final slices.
    """
    buckets = _bucketise(response)
    slices = [
        {"label": b.sub_category, "value": _f(b.planned_final)}
        for b in buckets
        if b.planned_final > 0
    ]
    if not slices:
        return None
    slices.sort(key=lambda s: -s["value"])

    return ChartSpec(
        chart_type="planned_donut",
        title="Your portfolio after rebalancing",
        caption="Share of corpus by category in the planned allocation.",
        data={"slices": slices},
    )


def compute_tax_cost_bar(response: RebalancingComputeResponse) -> ChartSpec | None:
    """Bar — exit-load + realised gains (ST/LT) per SEBI sub_category.

    Best for: "what does this rebalance cost me?" questions.
    Skipped when totals are all zero.
    """
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

    return ChartSpec(
        chart_type="tax_cost_bar",
        title="Cost of rebalancing per category",
        caption="Realised short-term and long-term gains plus exit loads.",
        data={
            "categories": [b.sub_category for b in rows],
            "series": [
                {"name": "Short-term gains", "values": [_f(b.realised_stcg) for b in rows]},
                {"name": "Long-term gains", "values": [_f(b.realised_ltcg) for b in rows]},
                {"name": "Exit load", "values": [_f(b.exit_load_inr) for b in rows]},
            ],
            "totals": {
                "tax_estimate_inr": _f(totals.total_tax_estimate_inr or Decimal(0)),
                "exit_load_inr": _f(totals.total_exit_load_inr or Decimal(0)),
            },
        },
    )


def available_charts(response: RebalancingComputeResponse) -> list[ChartSpec]:
    """Compute every applicable chart spec for ``response``.

    Returns only the ones whose computers produced a non-None spec — e.g.
    the tax-cost chart is skipped when there are no taxes or exit loads.
    """
    candidates = [
        compute_category_gap_bar(response),
        compute_planned_donut(response),
        compute_tax_cost_bar(response),
    ]
    return [c for c in candidates if c is not None]
