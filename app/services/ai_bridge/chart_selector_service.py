"""Caller for the chart_selector agent (Anthropic Haiku).

Builds the live catalogue from `CHART_TOOLS`, calls the agent's tool-forced
prompt against Anthropic, validates returned chart names against the
registry, and returns the validated subset. Designed to be kicked off as
`asyncio.create_task` in parallel with text generation, so its latency is
hidden behind the slower content path.
"""
from __future__ import annotations

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.registry import CHART_TOOLS

ensure_ai_agents_path()

from chart_selector import (
    PICK_CHARTS_TOOL_SCHEMA,
    SYSTEM_PROMPT,
    ChartCatalogueEntry,
    ChartSelectionInput,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT_SECONDS = 15.0
_MAX_TOKENS = 200


def _build_catalogue() -> list[ChartCatalogueEntry]:
    return [
        ChartCatalogueEntry(name=tool.name, description=tool.description)
        for tool in CHART_TOOLS.values()
    ]


async def select_charts(question: str, intent: str | None) -> list[str]:
    """Return validated chart names. Empty list on any failure or no match."""
    if not CHART_TOOLS:
        return []

    api_key = get_settings().get_anthropic_chart_selector_key()
    if not api_key:
        logger.warning("chart_selector_service: no Anthropic key configured; skipping")
        return []

    selection_input = ChartSelectionInput(
        question=question,
        intent=intent,
        catalogue=_build_catalogue(),
    )

    llm = ChatAnthropic(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        api_key=api_key,
        timeout=_TIMEOUT_SECONDS,
    ).bind_tools(
        [PICK_CHARTS_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "pick_charts"},
    )
    try:
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=build_user_prompt(selection_input)),
        ])
    except Exception as exc:
        logger.warning("chart_selector_service: API call failed (%s); returning empty", exc)
        return []

    raw_names: list[str] = []
    for tool_call in response.tool_calls:
        if tool_call["name"] == "pick_charts":
            args = tool_call["args"] or {}
            candidate = args.get("chart_names")
            if isinstance(candidate, list):
                raw_names = [n for n in candidate if isinstance(n, str)]
            break

    valid = [n for n in raw_names if n in CHART_TOOLS]
    if raw_names and not valid:
        logger.info(
            "chart_selector_service: LLM proposed %s but none matched registry",
            raw_names,
        )
    return valid
