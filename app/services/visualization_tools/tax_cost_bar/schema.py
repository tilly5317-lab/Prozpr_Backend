"""Pydantic payload — tax_cost_bar chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class TaxCostNamedSeries(BaseModel):
    name: str
    values: list[float]


class TaxCostTotals(BaseModel):
    tax_estimate_inr: float
    exit_load_inr: float


class TaxCostBar(ChartBase):
    type: Literal["tax_cost_bar"] = "tax_cost_bar"
    categories: list[str]
    series: list[TaxCostNamedSeries]
    totals: TaxCostTotals
    caption: str | None = None
