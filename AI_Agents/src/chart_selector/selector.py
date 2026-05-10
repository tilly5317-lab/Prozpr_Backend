"""Chart selector — prompt construction + Anthropic tool schema.

The HTTP call lives in `app/services/ai_bridge/chart_selector_service.py`,
matching the codebase convention (see intent_classifier_service). This module
owns the agent's prompts, types, and helpers — nothing FastAPI-specific.
"""
from __future__ import annotations

from .models import ChartSelectionInput

# Anthropic tool schema. Forced via `tool_choice` to guarantee structured output.
PICK_CHARTS_TOOL_SCHEMA: dict = {
    "name": "pick_charts",
    "description": "Return the subset of chart names that should be shown alongside the reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Chart names from the catalogue. Use exact names. "
                    "Empty array if none apply."
                ),
            }
        },
        "required": ["chart_names"],
    },
}


def build_user_prompt(input: ChartSelectionInput) -> str:
    """Construct the user-turn prompt sent to the LLM."""
    catalogue_block = "\n".join(
        f"- {entry.name}: {entry.description}" for entry in input.catalogue
    )
    return (
        f"User question: {input.question}\n"
        f"Classifier intent: {input.intent or 'unknown'}\n\n"
        f"Available charts (name: description):\n{catalogue_block}"
    )
