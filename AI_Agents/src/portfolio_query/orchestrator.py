"""
orchestrator.py — PortfolioQueryOrchestrator
=============================================
Prepares everything the portfolio_query intent needs, but does NOT answer:
the reply is written by the shared answer formatter in
``app/domains/ai_engine/answer_formatter`` (see ``AI_Agents/src/CLAUDE.md``).

What this owns:
  - ``query_body``   the skill prompt (portfolio_query.md) with guardrails.md
                     filled in — the in/out-of-scope Path X/M/P rules.
  - ``build_facts``  the three context sources as one INR-enriched dict: the
                     fund-house market commentary (only when asked for), the
                     client profile, and the client's current portfolio.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from common import format_inr_indian, read_text_bom_aware
from house_view import load_house_view

from .models import ClientContext, PortfolioContext
from .skill_executor import SkillExecutor


logger = logging.getLogger(__name__)


_VIEW_NOT_REQUESTED = (
    "(Not loaded — this question was routed as being about the customer's own "
    "portfolio without needing our market stance. Answer from their holdings and "
    "profile; do not speculate about Prozpr's market view.)"
)

_VIEW_UNAVAILABLE = (
    "(Our latest house view isn't on file right now. Answer from the customer's "
    "holdings and profile; do not invent a Prozpr market stance, and do not "
    "mention that the view is missing.)"
)


def _load_fund_house_view() -> str:
    """Prozpr's OWN market stance — the Prozpr-only slice (no fund house is named).
    Degrades to a placeholder (never raises): the view is optional by design."""
    return load_house_view(prozpr_only=True) or _VIEW_UNAVAILABLE


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


class PortfolioQueryOrchestrator:
    def __init__(self):
        module_root = Path(__file__).parent

        # BOM-aware read: these .md sources hold non-ASCII and must not depend
        # on the OS locale (cp1252 on Windows) or a stray UTF-16 BOM.
        self._guardrail_rules = read_text_bom_aware(module_root / "guardrails.md")
        self.query_skill = SkillExecutor(module_root / "portfolio_query.md")

    @property
    def query_body(self) -> str:
        """The System Prompt section with the guardrail rules filled in.

        For callers that own the LLM call themselves (the app bridge passes this
        to the shared answer formatter). Editing the skill or `guardrails.md`
        still changes behaviour with no code change.
        """
        return self.query_skill.render(guardrail_rules=self._guardrail_rules)[0]

    def build_facts(
        self,
        *,
        client: ClientContext,
        portfolio: PortfolioContext,
        want_fund_house_view: bool = False,
    ) -> dict:
        """The context sources as one INR-enriched dict.

        The fund-house view (Prozpr-only slice) degrades to a placeholder when
        unavailable — it is optional by design, loaded only when the classifier
        asks for it.
        """
        return {
            "fund_house_view": (
                _load_fund_house_view()
                if want_fund_house_view
                else _VIEW_NOT_REQUESTED
            ),
            "client_profile": _enrich_inr_fields(client.model_dump(exclude_none=True)),
            "current_portfolio": _enrich_inr_fields(
                portfolio.model_dump(exclude_none=True)
            ),
        }
