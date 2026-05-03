"""Pydantic schema — visualization_tools chart payloads (v1).

Typed payloads returned by chart tools. The frontend `ChartRenderer` dispatches on `type`. Schema is versioned (`schema_version`) so future changes can be additive.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

SCHEMA_VERSION = "v1"


class ChartBase(BaseModel):
    schema_version: Literal["v1"] = "v1"
    title: str
    subtitle: str | None = None


# allocation.current_donut

class DonutSlice(BaseModel):
    label: str
    value: float
    percentage: float = Field(..., ge=0, le=100)
    color_hint: str | None = None


class CurrentAllocationDonut(ChartBase):
    type: Literal["allocation.current_donut"] = "allocation.current_donut"
    total_value: float
    slices: list[DonutSlice]


# allocation.target_vs_actual

class TargetVsActualBar(BaseModel):
    asset_class: str
    target_pct: float
    actual_pct: float
    drift_pct: float


class TargetVsActualBars(ChartBase):
    type: Literal["allocation.target_vs_actual"] = "allocation.target_vs_actual"
    bars: list[TargetVsActualBar]


# allocation.sub_asset_treemap

class TreemapNode(BaseModel):
    label: str
    parent: str
    value: float


class SubAssetTreemap(ChartBase):
    type: Literal["allocation.sub_asset_treemap"] = "allocation.sub_asset_treemap"
    nodes: list[TreemapNode]


# allocation.concentration_risk

class ConcentrationHolding(BaseModel):
    label: str
    value: float
    percentage: float = Field(..., ge=0, le=100)


class ConcentrationRisk(ChartBase):
    type: Literal["allocation.concentration_risk"] = "allocation.concentration_risk"
    headline: str
    severity: Literal["ok", "watch", "act"]
    top_n: int
    top_holdings: list[ConcentrationHolding]
    rest_percentage: float
    rest_count: int


ChartPayload = Union[
    CurrentAllocationDonut,
    TargetVsActualBars,
    SubAssetTreemap,
    ConcentrationRisk,
]
