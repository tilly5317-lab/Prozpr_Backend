"""MutualFundQueryOrchestrator — pass 1 of the two-pass engine.

``extract`` names the fund(s) and classifies the ask in a single forced-tool
call; it must never answer the question. Pass 2 is the shared answer formatter
in ``app/domains/ai_engine/answer_formatter``, which narrates the app-built
``MutualFundQueryFacts`` using ``narrate_body`` as its body prompt.

All prompt content lives in the single ``mutual_fund_query.md`` file; the
guardrail rules are embedded in the Narrate System section.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from common import read_text_bom_aware

from .models import ConversationTurn, ExtractResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "mutual_fund_query.md"


_EXTRACT_TOOL = {
    "name": "return_extract_result",
    "description": (
        "Return the fund(s) the customer named and what they're asking for. Call "
        "exactly once; do NOT answer the question yourself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fund_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific mutual fund name(s) the customer is asking about, with "
                    "pronouns/references resolved from context. Empty list if none."
                ),
            },
            "asked_for": {
                "type": "string",
                "enum": ["reasoning", "returns", "comparison"],
                "description": (
                    "reasoning = why we recommend it; returns = its historical "
                    "returns; comparison = how it compares (to peers or another fund)."
                ),
            },
            "is_screen": {
                "type": "boolean",
                "description": (
                    "True when the customer names NO specific fund and asks for the "
                    "best/top performing funds in general (a ranked shortlist), e.g. "
                    "'which are the best performing mutual funds?', 'top large cap funds'. "
                    "False for any question about a specific named fund."
                ),
            },
            "screen_category": {
                "type": ["string", "null"],
                "description": (
                    "For a screen, the fund category/sub-category named, if any "
                    "(e.g. 'Large Cap', 'Mid Cap', 'Flexi Cap'). Null when the customer "
                    "asks for best funds overall with no category."
                ),
            },
            "screen_horizon_years": {
                "type": ["integer", "null"],
                "description": (
                    "For a screen, the return horizon in years if the customer named one "
                    "('this year' -> 1, 'over 5 years' -> 5). Null to use the default (3)."
                ),
            },
        },
        "required": ["fund_names", "asked_for"],
    },
}


def _section(body: str, heading: str) -> str:
    """Extract a ``## <heading>`` section up to the next ``##`` or end of file."""
    match = re.search(
        rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL
    )
    return match.group(1).strip() if match else ""


def _fill(template: str, variables: dict) -> str:
    """Replace ``{{name}}`` placeholders; single-brace (JSON) content is untouched."""
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: str(variables.get(m.group(1), m.group(0))),
        template,
    )


def _format_history(history: list[ConversationTurn]) -> str:
    if not history:
        return "(No prior conversation)"
    return "\n".join(
        f"{'User' if t.role == 'user' else 'Assistant'}: {t.content}" for t in history
    )


class MutualFundQueryOrchestrator:
    def __init__(self, llm_client):
        # BOM-aware: the prompt file holds non-ASCII (₹, em-dashes).
        content = read_text_bom_aware(_PROMPT_PATH)
        parts = content.split("---")
        self._meta = (yaml.safe_load(parts[1]) or {}) if len(parts) >= 3 else {}
        body = "---".join(parts[2:]) if len(parts) >= 3 else content
        self._extract_system = _section(body, "Extract System")
        self._extract_user = _section(body, "Extract User")
        self._narrate_system = _section(body, "Narrate System")
        self.llm = llm_client

    @property
    def narrate_body(self) -> str:
        """The Narrate System section, for callers that own the LLM call themselves.

        The app bridge passes this to the shared answer formatter instead of
        calling ``narrate`` — editing the skill still changes behaviour.
        """
        return self._narrate_system

    async def extract(
        self, question: str, conversation_history: list[ConversationTurn] | None = None
    ) -> ExtractResult:
        user = _fill(
            self._extract_user,
            {
                "conversation_history": _format_history(conversation_history or []),
                "question": question,
            },
        )
        data, usage = await self.llm.call_structured(
            model=self._meta.get("model", "haiku"),
            system=self._extract_system,
            user=user,
            tool=_EXTRACT_TOOL,
            max_tokens=self._meta.get("extract_max_tokens", 512),
        )
        return ExtractResult.model_validate(data)
