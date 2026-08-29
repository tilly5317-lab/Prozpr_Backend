"""Both send-message endpoints must auto-title a session's first turn.

The auto-titler shipped wired into ``send_message`` only — but the app talks to
``send_message_streaming``, so in production every session kept its "New Chat"
placeholder forever and the feature looked dead. These tests pin the gate
itself and, structurally, that BOTH endpoints use it, so the two paths cannot
drift apart again.

The endpoint check reads the router as text rather than importing it: the gate
lives in the (dependency-light) title service precisely so this file never has
to drag the whole app into the test process.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from app.domains.chat.services import chat_title_service
from app.domains.chat.services.chat_title_service import maybe_start_auto_title

_ROUTER_SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "routers" / "chat_router.py"
).read_text(encoding="utf-8")


@pytest.fixture
def titled(monkeypatch):
    """Replace the LLM call with a synchronous stand-in, and record the input."""
    seen: dict[str, str] = {}

    async def _fake(first_message: str, intent_name: str | None) -> str:
        seen["first_message"] = first_message
        return "Retirement Corpus Plan"

    monkeypatch.setattr(chat_title_service, "generate_chat_title", _fake)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["", "New Chat", "New conversation", "Pi Chat", None])
async def test_titles_a_first_turn_on_every_placeholder_name(title, titled):
    """Each placeholder the app can create a session with is titleable."""
    task = maybe_start_auto_title(
        current_title=title, has_history=False, first_message="Plan my retirement"
    )
    assert task is not None
    assert await task == "Retirement Corpus Plan"
    assert titled["first_message"] == "Plan my retirement"


@pytest.mark.asyncio
async def test_never_overwrites_a_name_the_user_chose(titled):
    assert (
        maybe_start_auto_title(
            current_title="My tax plan", has_history=False, first_message="hi"
        )
        is None
    )


@pytest.mark.asyncio
async def test_only_the_first_turn_is_titled(titled):
    """A non-empty history means the session was already named on turn one."""
    assert (
        maybe_start_auto_title(
            current_title="New Chat", has_history=True, first_message="hi"
        )
        is None
    )


@pytest.mark.asyncio
async def test_titling_runs_concurrently_with_the_caller(titled):
    """The gate must hand back a scheduled task, not an already-awaited value —
    that is what keeps titling off the reply's critical path."""
    task = maybe_start_auto_title(
        current_title="New Chat", has_history=False, first_message="hi"
    )
    assert isinstance(task, asyncio.Task)
    await task


def _endpoint_source(name: str) -> str:
    """The body of one router endpoint, up to the next top-level `def`."""
    start = _ROUTER_SRC.index(f"async def {name}(")
    rest = _ROUTER_SRC[start + 1 :]
    nxt = re.search(r"\n@router\.", rest)
    return rest[: nxt.start()] if nxt else rest


@pytest.mark.parametrize("endpoint", ["send_message", "send_message_streaming"])
def test_endpoint_starts_and_persists_the_auto_title(endpoint):
    """Regression guard for the bug: streaming skipped titling entirely."""
    src = _endpoint_source(endpoint)
    assert "maybe_start_auto_title" in src, f"{endpoint} does not start auto-titling"
    assert "session.title = await title_task" in src, (
        f"{endpoint} never persists the generated title"
    )
