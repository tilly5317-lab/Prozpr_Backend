"""Pydantic payload — category_gap_bar chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class NamedSeries(BaseModel):
    name: str
    values: list[float]


class CategoryGapBar(ChartBase):
    type: Literal["category_gap_bar"] = "category_gap_bar"
    categories: list[str]
    series: list[NamedSeries]
    caption: str | None = None
