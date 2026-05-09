"""Chart selector — decides which charts (if any) help the current turn.

One Haiku call per turn. Inputs: the user's question, the classified intent,
and the auto-built catalogue of registered chart tools (name + description).
Output: the subset of chart names the LLM judges relevant. Hallucinated names
are dropped before the result is returned.

Designed to be kicked off as `asyncio.create_task` in parallel with text
generation, so its latency is hidden behind the slower content path.
"""
from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.services.visualization_tools.registry import CHART_TOOLS

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT_SECONDS = 15.0
_MAX_TOKENS = 200

_SYSTEM_PROMPT = (
    "You decide which charts (if any) help illustrate the assistant's reply to "
    "the user. You are given the user's question, the classifier's intent label, "
    "and a catalogue of available charts. Pick only charts that are clearly "
    "relevant — never reach. Empty list is the right answer when no chart adds "
    "value. Always respond by calling the `pick_charts` tool exactly once."
)

_PICK_CHARTS_TOOL = {
    "name": "pick_charts",
    "description": "Return the subset of chart names that should be shown alongside the reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Chart names from the catalogue. Use exact names. Empty array if none apply.",
            }
        },
        "required": ["chart_names"],
    },
}


def _build_catalogue() -> str:
    return "\n".join(
        f"- {tool.name}: {tool.description}" for tool in CHART_TOOLS.values()
    )


async def select_charts(question: str, intent: str | None) -> list[str]:
    """Return validated chart names. Empty list on any failure or no match."""
    if not CHART_TOOLS:
        return []

    api_key = get_settings().get_anthropic_key()
    if not api_key:
        logger.warning("chart selector: no Anthropic key configured; skipping")
        return []

    user_prompt = (
        f"User question: {question}\n"
        f"Classifier intent: {intent or 'unknown'}\n\n"
        f"Available charts (name: description):\n{_build_catalogue()}"
    )

    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [_PICK_CHARTS_TOOL],
        "tool_choice": {"type": "tool", "name": "pick_charts"},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("chart selector: API call failed (%s); returning empty", exc)
        return []

    blocks = resp.json().get("content", [])
    raw_names: list[str] = []
    for b in blocks:
        if b.get("type") == "tool_use" and b.get("name") == "pick_charts":
            input_obj = b.get("input") or {}
            candidate = input_obj.get("chart_names")
            if isinstance(candidate, list):
                raw_names = [n for n in candidate if isinstance(n, str)]
            break

    valid = [n for n in raw_names if n in CHART_TOOLS]
    if raw_names and not valid:
        logger.info(
            "chart selector: LLM proposed %s but none matched registry", raw_names
        )
    return valid
