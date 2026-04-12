"""
LangChain LCEL pipeline for ideal mutual fund asset allocation.

5-step sequential pipeline — all steps are LLM calls (Claude Sonnet):
  Steps 1–5 use reference .md system prompts loaded at import time.
  Step 4 validates the Step 3 allocation against guardrail rules and
  corrects any violations, all within the LLM call.

Usage:
    from Ideal_asset_allocation.main import asset_allocation_chain
    from Ideal_asset_allocation.models import AllocationInput

    inputs = AllocationInput(
        effective_risk_score=7.0,
        age=35,
        annual_income=2000000,
        ...
    )
    result = asset_allocation_chain.invoke(inputs.model_dump())
    # result contains: inputs + step1_carve_outs + step2_asset_class +
    #                  step3_subgroups + step4_validation + step5_presentation
"""

import json
import logging
import re
import time
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from .models import AllocationInput, AllocationOutput
from .prompts import (
    step1_prompt,
    step2_prompt,
    step3_prompt,
    step4_prompt,
    step5_prompt,
    _serialize,
    _slim_for_step2,
    _slim_for_step3,
    _slim_for_step4,
    _slim_for_step5,
)

logger = logging.getLogger(__name__)

_llm_step1 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=2500)
_llm_step2 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=1500)
_llm_step3 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=3000)
_llm_step4 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=2500)
_llm_step5 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=4500)
_llm = _llm_step3  # default fallback


def _add_cache_control(messages: list) -> list:
    """Mark system messages with cache_control for Anthropic prompt caching.

    System prompts are static .md files loaded at import time, so they cache
    effectively across calls and reduce input token costs by ~90% on cache hits.
    """
    for msg in messages:
        if msg.type == "system":
            msg.additional_kwargs["cache_control"] = {"type": "ephemeral"}
    return messages


def _extract_text_content(content) -> str:
    """Normalize LLM response content to a plain text string.

    Handles plain strings (normal case) and list-of-blocks content
    (returned when thinking, citations, or multi-block responses are present).
    """
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
    """Robustly extract the first JSON object from raw LLM text.

    Handles markdown fences, trailing text after the JSON, and nested braces.
    Uses the JSONDecoder's raw_decode to stop at the first complete object.
    """
    raw = text.strip()

    if raw.startswith("```"):
        parts = raw.split("```")
        for part in parts[1:]:
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

    raise json.JSONDecodeError("No JSON object found in LLM response", raw, 0)


def _make_step(prompt, step_name: str = "unknown", state_slicer=None, llm=None):
    """
    Build a runnable that:
    1. Optionally slims the accumulated state via state_slicer
    2. Serializes the (slimmed) state as {state_json}
    3. Calls the LLM with the step's system + human prompts
    4. Parses the JSON response
    """
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
                logger.warning(
                    "[%s] JSON parse failed (attempt %s/3), %d chars: %s",
                    step_name,
                    attempt + 1,
                    len(raw),
                    e.msg,
                )
                time.sleep(1.0 * (attempt + 1))
        assert last_err is not None
        raise last_err
    return RunnableLambda(_run)


def _make_step5(prompt, step_name: str = "step5", state_slicer=None):
    """Same as _make_step but uses the higher token-limit LLM for Step 5."""
    def _run(state: dict):
        slim = state_slicer(state) if state_slicer else state
        messages = prompt.format_messages(state_json=_serialize(slim))
        _add_cache_control(messages)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            response = _llm_step5.invoke(messages)
            raw = _extract_text_content(response.content)
            try:
                return _extract_json(raw)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning(
                    "[%s] JSON parse failed (attempt %s/3), %d chars: %s",
                    step_name,
                    attempt + 1,
                    len(raw),
                    e.msg,
                )
                time.sleep(1.0 * (attempt + 1))
        assert last_err is not None
        raise last_err
    return RunnableLambda(_run)


# ── Pipeline ──────────────────────────────────────────────────────────────────

asset_allocation_chain = (
    RunnablePassthrough.assign(step1_carve_outs   = _make_step(step1_prompt,  "step1_carve_outs",                    llm=_llm_step1))
  | RunnablePassthrough.assign(step2_asset_class  = _make_step(step2_prompt,  "step2_asset_class",  _slim_for_step2, llm=_llm_step2))
  | RunnablePassthrough.assign(step3_subgroups    = _make_step(step3_prompt,  "step3_subgroups",    _slim_for_step3, llm=_llm_step3))
  | RunnablePassthrough.assign(step4_validation   = _make_step(step4_prompt,  "step4_validation",   _slim_for_step4, llm=_llm_step4))
  | RunnablePassthrough.assign(step5_presentation = _make_step5(step5_prompt, "step5_presentation", _slim_for_step5))
)


def run_allocation(inputs: AllocationInput) -> AllocationOutput:
    """
    Run the full 5-step allocation pipeline and return a validated AllocationOutput.
    Raises pydantic.ValidationError if the LLM response does not match the schema.
    """
    result = asset_allocation_chain.invoke(inputs.model_dump())
    return AllocationOutput.model_validate(result["step5_presentation"])
