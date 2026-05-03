"""Pydantic payload — planned_donut chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class PlannedDonutSlice(BaseModel):
    label: str
    value: float


class PlannedDonut(ChartBase):
    type: Literal["planned_donut"] = "planned_donut"
    slices: list[PlannedDonutSlice]
    caption: str | None = None
