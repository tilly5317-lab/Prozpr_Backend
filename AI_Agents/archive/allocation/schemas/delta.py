from typing import Literal
from pydantic import BaseModel


class DeltaItem(BaseModel):
    asset_class: str
    current_pct: float
    ideal_pct: float
    delta_pct: float
    direction: Literal["increase", "decrease", "hold"]


class Delta(BaseModel):
    items: list[DeltaItem]
