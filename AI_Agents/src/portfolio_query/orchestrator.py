"""
orchestrator.py — PortfolioQueryOrchestrator
=============================================
Handles the portfolio_query intent: answers client questions about their own
portfolio using three context sources — the fund house market commentary, the
client profile, and the client's current portfolio.

HOW IT WORKS (4-step pipeline)
--------------------------------
Step 1  Load market commentary
        Reads `AI_Agents/Reference_docs/market_commentary_latest.md` — the fund
        house's current Indian-market commentary (auto-refreshed by the
        `market_commentary` agent).

Step 2  Format context
        Serialises the client profile, current portfolio, and conversation
        history into strings ready for template injection.

Step 3  Call portfolio_query skill  [calls Claude Haiku]
        Runs portfolio_query.md in this package. The system prompt embeds the
        guardrail rules (from guardrails.md) and instructs the model to:
          - Check if the question is in scope.
          - If out of scope: return guardrail_triggered=true with a redirect message.
          - If in scope: answer factually from the three context sources.

Step 4  Return response
        Parses the JSON response from Claude into a PortfolioQueryResponse.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .llm_client import LLMClient
from .models import ClientContext, ConversationTurn, PortfolioContext, PortfolioQueryResponse
from .skill_executor import SkillExecutor


logger = logging.getLogger(__name__)


_MARKET_COMMENTARY_PATH = (
    Path(__file__).resolve().parents[2] / "Reference_docs" / "market_commentary_latest.md"
)


def _load_market_commentary() -> str:
    if not _MARKET_COMMENTARY_PATH.exists():
        raise FileNotFoundError(
            f"Market commentary file missing at {_MARKET_COMMENTARY_PATH}. "
            "Run the market_commentary agent (or trigger a general_market_query) first."
        )
    text = _MARKET_COMMENTARY_PATH.read_text()
    if not text.strip():
        raise ValueError(f"Market commentary file at {_MARKET_COMMENTARY_PATH} is empty")
    return text.strip()


class PortfolioQueryOrchestrator:
    def __init__(self, llm_client: LLMClient):
        module_root = Path(__file__).parent

        self._guardrail_rules = (module_root / "guardrails.md").read_text()
        self.query_skill = SkillExecutor(module_root / "portfolio_query.md", llm_client)

    def _format_history(self, history: list[ConversationTurn]) -> str:
        if not history:
            return "(No prior conversation)"
        lines = []
        for turn in history:
            label = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{label}: {turn.content}")
        return "\n".join(lines)

    async def run(
        self,
        question: str,
        client: ClientContext,
        portfolio: PortfolioContext,
        conversation_history: list[ConversationTurn] | None = None,
    ) -> PortfolioQueryResponse:
        history = conversation_history or []

        market_commentary = _load_market_commentary()
        logger.debug(
            "portfolio_query: market commentary loaded (%d words)",
            len(market_commentary.split()),
        )

        formatted_history = self._format_history(history)
        logger.debug("portfolio_query: %d prior turns formatted", len(history))

        data, usage = await self.query_skill.run(
            market_commentary=market_commentary,
            client_profile=client.model_dump_json(indent=2),
            current_portfolio=portfolio.model_dump_json(indent=2),
            conversation_history=formatted_history,
            question=question,
            guardrail_rules=self._guardrail_rules,
        )
        logger.debug(
            "portfolio_query: skill ok (in=%s out=%s)",
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )

        response = PortfolioQueryResponse(
            answer=data.get("answer"),
            guardrail_triggered=bool(data.get("guardrail_triggered", False)),
            redirect_message=data.get("redirect_message"),
        )
        if response.guardrail_triggered:
            logger.debug("portfolio_query: guardrail triggered")
        else:
            logger.debug("portfolio_query: in-scope answer generated")

        return response
