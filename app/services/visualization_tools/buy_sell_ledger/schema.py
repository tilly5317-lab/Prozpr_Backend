"""Pydantic payload — buy_sell_ledger chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class BuySellRow(BaseModel):
    name: str
    sub_category: str
    buy_inr: float
    sell_inr: float


class BuySellLedger(ChartBase):
    type: Literal["buy_sell_ledger"] = "buy_sell_ledger"
    rows: list[BuySellRow]
