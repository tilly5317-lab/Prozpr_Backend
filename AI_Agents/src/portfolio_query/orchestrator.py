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

from .models import ClientContext, PortfolioContext
from .skill_executor import SkillExecutor


logger = logging.getLogger(__name__)


_MARKET_COMMENTARY_PATH = (
    Path(__file__).resolve().parents[2]
    / "Reference_docs"
    / "market_commentary_latest.md"
)

# Keep the commentary under this limit so it can't dominate the prompt
# (mirrors general_chat's _MAX_COMMENTARY_CHARS).
_MAX_COMMENTARY_CHARS = 7000


_COMMENTARY_NOT_REQUESTED = (
    "(Not loaded — this question was routed as being about the customer's own "
    "portfolio, so no market view was fetched. Answer from their holdings and "
    "profile. Do NOT speculate about market conditions, valuations or outlook, "
    "and do not mention that commentary is missing — the customer did not ask "
    "for it. If the question genuinely does need a market view, say you can "
    "pull one up if they'd like.)"
)


def _load_market_commentary() -> str:
    if not _MARKET_COMMENTARY_PATH.exists():
        raise FileNotFoundError(
            f"Market commentary file missing at {_MARKET_COMMENTARY_PATH}. "
            "Run the market_commentary agent (or trigger a general_market_query) first."
        )
    text = read_text_bom_aware(_MARKET_COMMENTARY_PATH)
    if not text.strip():
        raise ValueError(
            f"Market commentary file at {_MARKET_COMMENTARY_PATH} is empty"
        )
    return text.strip()[:_MAX_COMMENTARY_CHARS]


_FUND_HOUSE_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "Reference_docs" / "fund_house_commentry.md"
)

# Loaded whole: no research-distillation step exists on this path — the facts
# pack goes straight to the compose formatter. (See the plan's portfolio
# compose-weight note; latency is explicitly accepted.)
_MAX_VIEW_CHARS = 90_000

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
    """Prozpr's monthly stance. Degrades to a placeholder (never raises): the
    file is hand-maintained and optional — a missing view must not fail a turn."""
    if not _FUND_HOUSE_VIEW_PATH.exists():
        return _VIEW_UNAVAILABLE
    text = read_text_bom_aware(_FUND_HOUSE_VIEW_PATH).strip()
    return text[:_MAX_VIEW_CHARS] if text else _VIEW_UNAVAILABLE


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
        want_market_commentary: bool = True,
        want_fund_house_view: bool = False,
    ) -> dict:
        """The context sources as one INR-enriched dict.

        Raises ``FileNotFoundError`` when the commentary is wanted but missing —
        the caller's cue to say so rather than answer without it. The fund-house
        view degrades to a placeholder instead (it is optional by design).
        """
        return {
            "market_commentary": (
                _load_market_commentary()
                if want_market_commentary
                else _COMMENTARY_NOT_REQUESTED
            ),
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
