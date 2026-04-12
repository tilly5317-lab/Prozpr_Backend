# project/backend/wealth_core/allocation_reasoning.py
# Uses Anthropic (Claude) only; no OpenAI. Example selection is by order (no embeddings).

from __future__ import annotations
import json
from typing import Dict, Any, List, Tuple

from .models import ClientSnapshot, StrategicAssetAllocation
from pydantic import BaseModel
from .ai_client import llm_chat
import os

# --- Load example cases for few-shot learning ---
EXAMPLES_PATH = os.path.join(os.path.dirname(__file__), "allocation_examples.json")
with open(EXAMPLES_PATH, "r", encoding="utf-8") as f:
    _raw_examples = json.load(f)
ALLOCATION_EXAMPLES = _raw_examples if isinstance(_raw_examples, list) else [_raw_examples]

# --- Guardrails: min/max for each asset class ---
ASSET_CLASS_GUARDRAILS = {
    "equities": {"min": 10, "max": 80},
    "fixed_income": {"min": 10, "max": 80},
    "alternatives": {"min": 0, "max": 30},
    "cash": {"min": 0, "max": 20},
}


class LLMAllocationOutput(BaseModel):
    """Temporary wrapper to capture both the model and the rationale in one AI call."""
    allocation: StrategicAssetAllocation
    rationale: str


def serialize_client_input(client: ClientSnapshot) -> str:
    """Convert client snapshot to a string for the LLM."""
    return (
        f"Age: {client.background.age}, "
        f"Occupation: {client.background.occupation}, "
        f"Goals: {[g.description for g in client.goals]}, "
        f"Risk Tolerance: {client.risk_tolerance.overall_risk_tolerance}, "
        f"Time Horizon: {client.time_horizon.total_horizon_years}, "
        f"Return Objective: {client.return_objective.primary_objectives}, "
        f"Annual Income: {client.annual_income}, "
        f"Annual Expenses: {client.annual_expenses}, "
        f"Assets: {client.total_equities}, {client.total_debt}, {client.total_cash_bank}, etc."
    )


def select_examples(client: ClientSnapshot, n: int = 5) -> List[Dict]:
    """Return first n examples (no embeddings; Anthropic-only stack)."""
    return ALLOCATION_EXAMPLES[:n]


def apply_guardrails(allocation_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Clamps numeric fields and ensures sum is 100%."""
    for k, limits in ASSET_CLASS_GUARDRAILS.items():
        if k in allocation_dict and allocation_dict[k] is not None:
            allocation_dict[k] = max(limits["min"], min(allocation_dict[k], limits["max"]))

    numeric_parts = {k: v for k, v in allocation_dict.items() if isinstance(v, (int, float))}
    total = sum(numeric_parts.values())
    if total > 0:
        for k in numeric_parts:
            allocation_dict[k] = round((numeric_parts[k] / total) * 100, 2)
    return allocation_dict


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """Try to parse a JSON object from model output (may be wrapped in markdown)."""
    text = text.strip()
    # Strip markdown code block if present
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end].strip()
    return json.loads(text)


def derive_strategic_asset_allocation(client: ClientSnapshot) -> Tuple[StrategicAssetAllocation, str]:
    """Main entry point: Generates allocation using Anthropic (Claude) with few-shot examples."""
    examples = select_examples(client)

    prompt_context = (
        "You are an expert wealth planner. Given client details and example allocations, "
        "provide a StrategicAssetAllocation and a brief rationale. "
        "Respond with a single JSON object with two keys: 'allocation' (object with numeric fields like equities, fixed_income, alternatives, cash; values can be percentages) and 'rationale' (string).\n\n"
    )
    for ex in examples:
        prompt_context += f"Input: {ex.get('input', ex)}\nAllocation: {ex.get('allocation', {})}\nRationale: {ex.get('rationale', '')}\n---\n"
    prompt_context += f"\nNow provide a StrategicAssetAllocation for this client: {serialize_client_input(client)}"

    messages = [{"role": "user", "content": prompt_context}]

    try:
        raw = llm_chat(messages=messages, max_tokens=1024, temperature=0.3)
        data = _extract_json_from_text(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"LLM did not return valid JSON: {e}") from e

    allocation_data = data.get("allocation") or data
    rationale = data.get("rationale") or ""

    if isinstance(allocation_data, dict):
        guarded = apply_guardrails({k: v for k, v in allocation_data.items() if v is not None})
        saa = StrategicAssetAllocation(**guarded)
    else:
        saa = StrategicAssetAllocation()
    return saa, rationale
