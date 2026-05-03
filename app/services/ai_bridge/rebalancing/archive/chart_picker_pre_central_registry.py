"""LLM-driven chart picker for the rebalancing chat reply.

Mirrors ``app/services/ai_bridge/asset_allocation/chat.py:_detect_action``:
one Haiku call with structured Pydantic output. Sees the customer's question
and a compact summary of the candidate charts (titles + key signals), and
picks the most useful one to surface alongside the markdown brief.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_bridge.rebalancing.charts import ChartSpec, ChartType


logger = logging.getLogger(__name__)


# Single source of truth for chart-type → trigger guidance. Adding a new
# chart_type just means adding an entry here; the system prompt below updates
# automatically via f-string interpolation. Keep keys aligned to ``ChartType``.
_CHART_TRIGGERS: dict[str, str] = {
    "category_gap_bar": (
        "the user asks about gaps, drift, \"how off am I\", \"what should I be "
        "holding\", or generic \"rebalance my portfolio\" with no specific framing."
    ),
    "planned_donut": (
        "the user asks about the resulting/final portfolio shape, \"what will it "
        "look like\", or proportions."
    ),
    "tax_cost_bar": (
        "the user asks about cost, taxes, exit loads, \"is it worth it\", or "
        "trade-offs."
    ),
}

_PREFERENCES = "\n".join(
    f"- ``{name}`` when {desc}" for name, desc in _CHART_TRIGGERS.items()
)

_DETECT_SYSTEM = f"""You pick the single most useful chart to render alongside a rebalancing trade plan in chat.

Consider:
- The customer's question (what they want to understand).
- The set of available charts and their key signals.

Pick exactly one chart. Prefer:
{_PREFERENCES}

If none of the above clearly fits this question, return null for `chart_type` — no chart will be rendered.
"""


class ChartChoice(BaseModel):
    """LLM-picked chart selection."""

    chart_type: Optional[ChartType] = Field(
        default=None,
        description=(
            "The chart type to render. Must be one of the available types, OR "
            "null if no chart is genuinely useful for this question."
        ),
    )
    reason: str = Field(
        ..., description="One short sentence on why this chart fits — or why none fits — the question.",
        max_length=240,
    )


def _summarise(spec: ChartSpec) -> dict[str, Any]:
    """Compact, low-token summary of a candidate chart for the picker prompt."""
    summary: dict[str, Any] = {
        "chart_type": spec.chart_type,
        "title": spec.title,
    }
    data = spec.data
    if spec.chart_type == "category_gap_bar":
        cats = data.get("categories", [])
        series = {s["name"]: s["values"] for s in data.get("series", [])}
        gaps = []
        for i, name in enumerate(cats):
            current = (series.get("Current") or [0])[i]
            target = (series.get("Target") or [0])[i]
            gaps.append({"category": name, "gap": round(target - current)})
        summary["top_gaps"] = sorted(gaps, key=lambda g: -abs(g["gap"]))[:3]
    elif spec.chart_type == "planned_donut":
        slices = data.get("slices", [])
        total = sum(s["value"] for s in slices) or 1
        summary["top_slices"] = [
            {"label": s["label"], "share_pct": round(s["value"] / total * 100, 1)}
            for s in slices[:3]
        ]
    elif spec.chart_type == "tax_cost_bar":
        totals = data.get("totals", {})
        summary["tax_estimate_inr"] = round(totals.get("tax_estimate_inr", 0))
        summary["exit_load_inr"] = round(totals.get("exit_load_inr", 0))
    return summary


async def pick_chart(
    candidates: list[ChartSpec], user_question: str,
) -> ChartSpec | None:
    """Pick one chart from ``candidates`` based on ``user_question``.

    Returns ``None`` when (a) there are no candidates, or (b) the LLM concludes
    no chart is genuinely useful for the question (``chart_type=null``). On any
    LLM failure or unavailable-chart-type response, falls back to the first
    candidate so a chart still renders — silent fallback, never raises.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    by_type: dict[str, ChartSpec] = {c.chart_type: c for c in candidates}

    try:
        api_key = get_settings().get_anthropic_rebalancing_key()
        llm = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            api_key=api_key,
            max_tokens=200,
        ).with_structured_output(ChartChoice)

        summaries = [_summarise(c) for c in candidates]
        user_block = (
            f"Customer's question: {user_question}\n\n"
            f"Available charts (pick exactly one chart_type, OR set chart_type to null if none fits):\n"
            f"{json.dumps(summaries, default=str)}"
        )
        choice = await _ainvoke(llm, _DETECT_SYSTEM, user_block)
        if choice.chart_type is None:
            logger.info("rebalancing chart_picker chose no-chart reason=%s", choice.reason)
            return None
        picked = by_type.get(choice.chart_type)
        if picked is not None:
            logger.info(
                "rebalancing chart_picker chose=%s reason=%s",
                choice.chart_type, choice.reason,
            )
            return picked
        logger.warning(
            "rebalancing chart_picker chose unavailable type=%s; falling back",
            choice.chart_type,
        )
    except Exception as exc:
        logger.warning("rebalancing chart_picker failed (%s); using first candidate", exc)

    return candidates[0]


async def _ainvoke(llm: Any, system_text: str, user_text: str) -> Any:
    """Structured-output invocation with prompt-cached system prefix."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    return await asyncio.to_thread(llm.invoke, messages)
