"""
7-step LangChain LCEL pipeline for goal-based asset allocation.

Steps:
  1. step1_emergency     — emergency carve-out
  2. step2_short_term    — short-term goals (<24m)
  3. step3_medium_term   — medium-term goals (24–60m)
  4. step4_long_term     — long-term goals (>60m) + leftover
  5. step5_aggregation   — subgroup × bucket matrix
  6. step6_guardrails    — validation + fund mapping
  7. step7_presentation  — final client output

Usage:
    from goal_based_allocation.main import run_allocation
    from goal_based_allocation.models import AllocationInput, Goal

    result = run_allocation(AllocationInput(...))
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

_agents_root = Path(__file__).resolve().parents[3]
_backend_root = _agents_root.parent
for _env_path in [_agents_root / ".env", _backend_root / ".env"]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

_API_KEY = os.environ.get("asset_allocation_key")

from .models import AllocationInput, GoalAllocationOutput
from .prompts import (
    step1_prompt, step2_prompt, step3_prompt, step4_prompt,
    step5_prompt, step6_prompt, step7_prompt,
    _serialize,
    _slim_for_step2, _slim_for_step3, _slim_for_step4,
    _slim_for_step5, _slim_for_step6, _slim_for_step7,
)

logger = logging.getLogger(__name__)

_llm = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=3000, api_key=_API_KEY, temperature=0)
_llm_step7 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=5000, api_key=_API_KEY, temperature=0)


def _add_cache_control(messages: list) -> list:
    for msg in messages:
        if msg.type == "system":
            msg.additional_kwargs["cache_control"] = {"type": "ephemeral"}
    return messages


def _extract_text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _extract_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        for part in raw.split("```")[1:]:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                obj, _ = json.JSONDecoder().raw_decode(candidate)
                return obj
    if raw.startswith("{"):
        obj, _ = json.JSONDecoder().raw_decode(raw)
        return obj
    match = re.search(r"\{", raw)
    if match:
        obj, _ = json.JSONDecoder().raw_decode(raw[match.start():])
        return obj
    raise json.JSONDecodeError("No JSON object found", raw, 0)


def _make_step(prompt, step_name: str, state_slicer=None, llm=None):
    _used_llm = llm or _llm

    def _run(state: dict):
        slim = state_slicer(state) if state_slicer else state
        messages = prompt.format_messages(state_json=_serialize(slim))
        _add_cache_control(messages)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            response = _used_llm.invoke(messages)
            raw = _extract_text_content(response.content)
            try:
                return _extract_json(raw)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning("[%s] JSON parse failed (attempt %s/3): %s",
                               step_name, attempt + 1, e.msg)
                time.sleep(1.0 * (attempt + 1))
        raise last_err

    return RunnableLambda(_run)


# ── Pipeline ──────────────────────────────────────────────────────────────────

goal_allocation_chain = (
    RunnablePassthrough.assign(step1_emergency    = _make_step(step1_prompt, "step1_emergency"))
  | RunnablePassthrough.assign(step2_short_term   = _make_step(step2_prompt, "step2_short_term",   _slim_for_step2))
  | RunnablePassthrough.assign(step3_medium_term  = _make_step(step3_prompt, "step3_medium_term",  _slim_for_step3))
  | RunnablePassthrough.assign(step4_long_term    = _make_step(step4_prompt, "step4_long_term",    _slim_for_step4))
  | RunnablePassthrough.assign(step5_aggregation  = _make_step(step5_prompt, "step5_aggregation",  _slim_for_step5))
  | RunnablePassthrough.assign(step6_guardrails   = _make_step(step6_prompt, "step6_guardrails",   _slim_for_step6))
  | RunnablePassthrough.assign(step7_presentation = _make_step(step7_prompt, "step7_presentation", _slim_for_step7, llm=_llm_step7))
)


def run_allocation(inputs: AllocationInput) -> GoalAllocationOutput:
    """Run the 7-step pipeline and return a validated GoalAllocationOutput."""
    result = goal_allocation_chain.invoke(inputs.model_dump())
    return GoalAllocationOutput.model_validate(result["step7_presentation"])
