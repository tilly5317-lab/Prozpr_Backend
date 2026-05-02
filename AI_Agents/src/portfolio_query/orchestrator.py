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

import json
import logging
from pathlib import Path
from typing import Any

from common import format_inr_indian

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


def _enrich_inr_fields(obj: Any) -> Any:
    """Walk a dict/list and add ``*_indian`` siblings to any ``*_inr`` field.

    The Indian-notation strings are pre-computed by ``format_inr_indian`` so the
    LLM never has to convert raw rupees at inference time (Haiku frequently
    drops an order of magnitude on lakh/crore boundaries). The system prompt
    instructs the LLM to copy these strings verbatim instead of computing.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out[k] = _enrich_inr_fields(v)
            if isinstance(k, str) and k.endswith("_inr") and v is not None:
                out[f"{k[:-4]}_indian"] = format_inr_indian(v)
        return out
    if isinstance(obj, list):
        return [_enrich_inr_fields(item) for item in obj]
    return obj


def _dump_enriched_json(model: Any) -> str:
    """Serialise a pydantic model to JSON with ``*_indian`` siblings injected."""
    return json.dumps(_enrich_inr_fields(model.model_dump()), indent=2, default=str)


# Tool schema forced on the portfolio_query LLM call. Anthropic returns a
# tool_use block whose ``input`` matches this schema, so we never parse JSON
# from raw text or strip markdown fences. The shape mirrors
# ``PortfolioQueryResponse`` (validated below).
_PORTFOLIO_QUERY_TOOL = {
    "name": "return_portfolio_query_response",
    "description": (
        "Return the final structured reply for a portfolio_query turn. Call this "
        "exactly once at the end of your turn — do NOT emit any free-text response "
        "outside this tool call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "guardrail_triggered": {
                "type": "boolean",
                "description": (
                    "True if the question is out-of-scope per the guardrail rules. "
                    "When true, set ``answer`` to null and provide ``redirect_message``."
                ),
            },
            "answer": {
                "type": ["string", "null"],
                "description": (
                    "Factual reply to the customer's in-scope question. Set to null "
                    "when ``guardrail_triggered`` is true."
                ),
            },
            "redirect_message": {
                "type": ["string", "null"],
                "description": (
                    "Polite, one-sentence redirect when ``guardrail_triggered`` is "
                    "true. Set to null when the answer is in-scope."
                ),
            },
        },
        "required": ["guardrail_triggered"],
    },
}


class PortfolioQueryOrchestrator:
    def __init__(self, llm_client: LLMClient):
        module_root = Path(__file__).parent

        self._guardrail_rules = (module_root / "guardrails.md").read_text()
        self.query_skill = SkillExecutor(module_root / "portfolio_query.md")
        self.llm = llm_client

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

        system, user = self.query_skill.render(
            market_commentary=market_commentary,
            client_profile=_dump_enriched_json(client),
            current_portfolio=_dump_enriched_json(portfolio),
            conversation_history=formatted_history,
            question=question,
            guardrail_rules=self._guardrail_rules,
        )
        meta = self.query_skill.meta
        data, usage = await self.llm.call_structured(
            model=meta.get("model", "haiku"),
            system=system,
            user=user,
            tool=_PORTFOLIO_QUERY_TOOL,
            max_tokens=meta.get("max_tokens", 1024),
        )
        logger.debug(
            "portfolio_query: skill ok (in=%s out=%s)",
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )

        # Pydantic validation as defence-in-depth — Anthropic guarantees the
        # tool input matches the schema, but we still want a typed model.
        response = PortfolioQueryResponse.model_validate(data)
        if response.guardrail_triggered:
            logger.debug("portfolio_query: guardrail triggered")
        else:
            logger.debug("portfolio_query: in-scope answer generated")

        return response
