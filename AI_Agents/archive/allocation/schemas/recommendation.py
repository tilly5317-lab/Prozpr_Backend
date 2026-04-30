from typing import Literal
from pydantic import BaseModel


class ActionItem(BaseModel):
    asset_class: str
    direction: Literal["increase", "decrease"]
    current_pct: float
    target_pct: float
    delta_pct: float
    fund_type: str
    action: str
    reason: str


class Recommendation(BaseModel):
    narrative: str
    action_items: list[ActionItem]
    confidence: Literal["high", "medium", "low"]
    disclaimers: list[str]
