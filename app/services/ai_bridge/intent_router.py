"""Shared classifier mechanics for per-module chat handlers.

Each module supplies its own typed Pydantic action model + system prompt;
this helper does the langchain-anthropic Haiku call + structured-output binding.
Replaces the duplicated _detect_action / _detect_rebal_action LLM call paths.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Type, TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_TOKENS = 400


async def classify_action(
    *,
    action_model: Type[T],
    system_prompt: str,
    user_block: str,
    api_key: str,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> T:
    """Single Haiku call with structured output bound to `action_model`."""
    llm = ChatAnthropic(
        model=model, api_key=api_key, max_tokens=max_tokens,
    ).with_structured_output(action_model)

    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_block),
    ]
    return await asyncio.to_thread(llm.invoke, messages)
