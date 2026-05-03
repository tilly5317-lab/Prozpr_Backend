"""Pydantic payload — concentration_risk chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.visualization_tools._base import ChartBase


class ConcentrationHolding(BaseModel):
    label: str
    value: float
    percentage: float = Field(..., ge=0, le=100)


class ConcentrationRisk(ChartBase):
    type: Literal["concentration_risk"] = "concentration_risk"
    headline: str
    severity: Literal["ok", "watch", "act"]
    top_n: int
    top_holdings: list[ConcentrationHolding]
    rest_percentage: float
    rest_count: int
