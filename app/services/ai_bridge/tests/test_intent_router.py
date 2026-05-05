"""classify_action() — the shared classifier helper used by every chat module."""

from __future__ import annotations

from typing import Literal, Optional
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel


class _DummyAction(BaseModel):
    mode: Literal["narrate", "redirect"]
    redirect_reason: Optional[str] = None


@pytest.mark.asyncio
async def test_classify_action_calls_haiku_with_structured_output() -> None:
    from app.services.ai_bridge.intent_router import classify_action

    fake_llm_invoke = MagicMock(
        return_value=_DummyAction(mode="redirect", redirect_reason="x"),
    )
    structured_llm = MagicMock(invoke=fake_llm_invoke)

    fake_chat_anthropic = MagicMock()
    fake_chat_anthropic.with_structured_output = MagicMock(return_value=structured_llm)

    with patch(
        "app.services.ai_bridge.intent_router.ChatAnthropic",
        return_value=fake_chat_anthropic,
    ):
        result = await classify_action(
            action_model=_DummyAction,
            system_prompt="route this",
            user_block="hello",
            api_key="dummy-key",
        )

    fake_chat_anthropic.with_structured_output.assert_called_once_with(_DummyAction)
    assert result.mode == "redirect"
    assert result.redirect_reason == "x"
