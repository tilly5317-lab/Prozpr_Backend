from pydantic import BaseModel, Field
from src.intent_classifier.models import Intent


class RouterResponse(BaseModel):
    intent: Intent
    answer: str
    module_used: str   # e.g. "market_commentary" or "stub"
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
