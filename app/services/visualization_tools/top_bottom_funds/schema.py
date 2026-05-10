"""Pydantic payload — top_bottom_funds chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class FundReturnRow(BaseModel):
    name: str
    return_pct: float
    current_value: float


class TopBottomFunds(ChartBase):
    type: Literal["top_bottom_funds"] = "top_bottom_funds"
    top: list[FundReturnRow]
    bottom: list[FundReturnRow]
    portfolio_average_pct: float
