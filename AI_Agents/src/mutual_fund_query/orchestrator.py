"""MutualFundQueryOrchestrator — the two-pass single-shot engine.

Pass 1 (extract): name the fund(s) + classify the ask. Pass 2 (narrate): render a
grounded ``MutualFundQueryFacts`` (built by the app-layer) into the customer reply
under the guardrail rules. Both passes are single forced-tool calls — no agentic
loop. The engine is DB-agnostic; it only ever sees the DTOs.

All prompt content lives in the single ``mutual_fund_query.md`` file, split into
four sections (Extract System / Extract User / Narrate System / Narrate User);
the guardrail rules are embedded in the Narrate System section.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import yaml

from common import read_text_bom_aware

from .models import (
    ConversationTurn,
    ExtractResult,
    MutualFundQueryFacts,
    MutualFundQueryResponse,
)

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

_NARRATE_TOOL = {
    "name": "return_mutual_fund_query_response",
    "description": (
        "Return the final reply for the fund question. Call exactly once at the end "
        "of your turn — do NOT emit any free-text response outside this tool call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The reply, grounded ONLY in the provided facts.",
            },
            "clarifying_question": {
                "type": ["string", "null"],
                "description": (
                    "If the fund is unclear/ambiguous, ask this instead of answering; "
                    "otherwise null."
                ),
            },
        },
        "required": ["answer"],
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
        self._narrate_user = _section(body, "Narrate User")
        self.llm = llm_client

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

    async def narrate(
        self,
        facts: MutualFundQueryFacts,
        question: str,
        conversation_history: list[ConversationTurn] | None = None,
    ) -> MutualFundQueryResponse:
        user = _fill(
            self._narrate_user,
            {
                "facts_json": json.dumps(
                    facts.model_dump(exclude_none=True),
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                ),
                "conversation_history": _format_history(conversation_history or []),
                "question": question,
            },
        )
        from persona import build_system_prompt  # shared PI voice (AI_Agents/src)

        system = build_system_prompt(
            self._narrate_system, format_profile="chat", question_aware=True
        )
        data, usage = await self.llm.call_structured(
            model=self._meta.get("model", "haiku"),
            system=system,
            user=user,
            tool=_NARRATE_TOOL,
            max_tokens=self._meta.get("narrate_max_tokens", 1024),
        )
        return MutualFundQueryResponse.model_validate(data)
