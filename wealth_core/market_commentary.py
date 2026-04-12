from __future__ import annotations
from typing import List, Dict
from .ai_client import llm_chat
import logging

logger = logging.getLogger(__name__)

MARKET_COMMENTARY_SYSTEM_PROMPT = """
You are a market commentary assistant for a wealth advisor.

You will receive:
- The client's latest question or comment about markets, macro, or portfolio performance.
- Optional context on the client's high-level profile (risk level, key goals).

Your tasks:
- Provide clear, calm explanations of market conditions and potential impact on portfolios.
- Reference the advisor's internal views and risk management philosophy described below.
- Do NOT change or suggest changing the client's risk profile, strategic asset allocation,
  or IPS parameters. You are only explaining and contextualizing current conditions.

Internal house view (to be edited by the firm):
[Insert your market commentary / house view text here]
"""

def generate_market_commentary_response(
    user_message: str,
    session_history: List[Dict],
    profile_context: Dict | None = None,
) -> str:
    messages = [{"role": "system", "content": MARKET_COMMENTARY_SYSTEM_PROMPT}]

    if profile_context:
        messages.append({
            "role": "system",
            "content": (
                f"Client profile context: risk tolerance={profile_context.get('risk_tolerance')}, "
                f"primary objective={profile_context.get('primary_objective')}, "
                f"time horizon={profile_context.get('time_horizon')}."
            ),
        })

    for entry in session_history:
        messages.append({"role": entry["role"], "content": entry["content"]})

    messages.append({"role": "user", "content": user_message})

    try:
        ai_message = llm_chat(
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        return ai_message
    except Exception as e:
        logger.error(f"Anthropic error in market commentary: {e}")
        return (
            "I'm currently unable to access my full market commentary engine. "
            "However, your existing portfolio is built for your long-term objectives, "
            "and short-term volatility is expected."
        )
