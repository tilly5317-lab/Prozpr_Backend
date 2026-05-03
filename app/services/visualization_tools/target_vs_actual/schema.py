"""Pydantic payload — target_vs_actual chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class TargetVsActualBar(BaseModel):
    asset_class: str
    target_pct: float
    actual_pct: float
    drift_pct: float


class TargetVsActual(ChartBase):
    type: Literal["target_vs_actual"] = "target_vs_actual"
    bars: list[TargetVsActualBar]
