from typing import Optional
from pydantic import BaseModel


class PortfolioQueryResponse(BaseModel):
    answer: Optional[str] = None
    guardrail_triggered: bool
    redirect_message: Optional[str] = None
