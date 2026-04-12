from typing import Literal
from pydantic import BaseModel
from .allocation import IdealAllocation
from .portfolio import Portfolio
from .delta import Delta
from .recommendation import ActionItem


class AllocationResponse(BaseModel):
    recommended_allocation: IdealAllocation
    current_allocation: Portfolio | None
    delta: Delta | None
    narrative: str
    action_items: list[ActionItem]
    confidence: Literal["high", "medium", "low"]
    disclaimers: list[str]
