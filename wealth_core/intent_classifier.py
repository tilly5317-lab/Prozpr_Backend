from __future__ import annotations
from typing import Literal

InteractionType = Literal["profile_update", "market_commentary"]

def classify_message_intent(
    latest_user_message: str,
    conv_state_snapshot: dict
) -> InteractionType:
    """
    Very simple initial classifier.
    Later you can upgrade to an LLM-based classifier using a separate prompt.
    """
    text = latest_user_message.lower()

    # Keywords that usually mean profile changes:
    profile_keywords = [
        "salary", "income", "lost my job", "new job", "bonus", "inheritance",
        "bought a house", "sold my house", "loan", "liability", "mortgage",
        "goal", "change my goal", "retirement age", "risk tolerance",
        "i can take more risk", "i want less risk", "time horizon",
        "emergency fund", "expense", "cash flow"
    ]

    for kw in profile_keywords:
        if kw in text:
            return "profile_update"

    # Otherwise default to market commentary
    return "market_commentary"
