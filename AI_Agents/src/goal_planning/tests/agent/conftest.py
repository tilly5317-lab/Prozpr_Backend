"""Test fixtures for agent E2E."""
from __future__ import annotations
from datetime import date

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver


class FakeChatAnthropic(ChatAnthropic):
    """Returns canned AIMessages without HTTP. Avoids SDK retry semantics in tests."""

    def __init__(self, responses: list[AIMessage], **kwargs):
        # Skip super().__init__ to avoid network/auth setup
        self._responses = iter(responses)
        for k, v in kwargs.items():
            try:
                setattr(self, k, v)
            except Exception:
                pass

    def invoke(self, messages, **kwargs) -> AIMessage:
        return next(self._responses)

    async def ainvoke(self, messages, **kwargs) -> AIMessage:
        return next(self._responses)

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def fresh_memory_saver() -> MemorySaver:
    """Per-test MemorySaver — avoids state bleed across tests."""
    return MemorySaver()


@pytest.fixture
def fake_llm_factory():
    def _build(*responses: AIMessage) -> FakeChatAnthropic:
        return FakeChatAnthropic(responses=list(responses))
    return _build


@pytest.fixture
def anchor_date() -> date:
    return date(2026, 5, 9)
