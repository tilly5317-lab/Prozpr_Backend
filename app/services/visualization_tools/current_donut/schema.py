"""Pydantic payload — current_donut chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.visualization_tools._base import ChartBase


class DonutSlice(BaseModel):
    label: str
    value: float
    percentage: float = Field(..., ge=0, le=100)
    color_hint: str | None = None


class CurrentDonut(ChartBase):
    type: Literal["current_donut"] = "current_donut"
    total_value: float
    slices: list[DonutSlice]
