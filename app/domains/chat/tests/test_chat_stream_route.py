"""SSE chat route: event wiring, ordering, and the authoritative done event.

Drives the real route through FastAPI with auth and the DB overridden, so it
never touches a database or an LLM. What it proves is the plumbing: that a
delta published from deep inside a turn reaches the wire as an SSE event, in
order, before a terminal done.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.all_models  # noqa: F401  - registers every mapper before ORM use
from app.core.database import get_db
from app.core.dependencies import get_ai_user_context, get_effective_user
from app.domains.ai_engine.streaming import current_token_stream
from app.domains.chat.models.chat import (
    CTA_PREFERENCES,
    ChatMessageRole,
    ChatSessionStatus,
)
from app.domains.chat.routers import chat_router as mod

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

ANSWER_PIECES = ["Your portfolio ", "is well ", "diversified."]
FULL_ANSWER = "".join(ANSWER_PIECES)


class _FakeDB:
    """Enough of AsyncSession for the route: add/commit/refresh."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        # Stand in for the DB defaults the route reads back after refresh.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, _obj, attribute_names=None):
        pass


class _FakeBrain:
    """Publishes deltas the way the real answer LLM does, then returns the
    turn result. Deliberately returns text DIFFERENT from the deltas to prove
    the done event is authoritative rather than a replay of what was streamed."""

    def __init__(self, *, final_text=FULL_ANSWER, raise_exc=None, pill=False):
        self._final = final_text
        self._raise = raise_exc
        self._pill = pill

    async def run_turn(self, _turn):
        if self._raise:
            raise self._raise
        stream = current_token_stream()
        assert stream is not None, "turn ran with no token stream in context"
        for piece in ANSWER_PIECES:
            stream.publish(piece)
        return SimpleNamespace(
            content=self._final,
            intent="portfolio_query",
            intent_confidence=0.91,
            intent_reasoning="asked about holdings",
            chart_payloads=None,
            asset_allocation_run_id=None,
            ideal_allocation_rebalancing_id=None,
            ideal_allocation_snapshot_id=None,
            additional_investment_run_id=None,
            additional_investment_cadence=None,
            has_candidate_preference=False,
            portfolio_data_missing=False,
            show_preferences_pill=self._pill,
        )


def _client(brain, db=None):
    app = FastAPI()
    app.include_router(mod.router)
    user = SimpleNamespace(id=USER_ID)
    db = db or _FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_effective_user] = lambda: user
    app.dependency_overrides[get_ai_user_context] = lambda: user
    # `title` is settable and required: the route assigns the auto-title onto the
    # session in the same commit, so a fake without it raises mid-stream.
    session = SimpleNamespace(
        id=SESSION_ID, status=ChatSessionStatus.active, title="New Chat"
    )

    async def _no_session_lookup(*_a, **_k):
        return session

    async def _no_history(*_a, **_k):
        return []

    stack = [
        patch.object(mod, "_get_user_session", _no_session_lookup),
        patch.object(mod, "load_conversation_history", _no_history),
        patch.object(mod, "ChatBrain", lambda: brain),
        patch.object(mod, "clear_thinking", lambda *_a, **_k: None),
    ]
    return TestClient(app), stack


def _events(body: str):
    """Parse an SSE body into (event, data) pairs."""
    out = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        out.append((name, data))
    return out


def _run(brain):
    client, stack = _client(brain)
    for p in stack:
        p.start()
    try:
        with client.stream(
            "POST",
            f"/chat/sessions/{SESSION_ID}/messages/stream",
            json={"content": "how is my portfolio?"},
        ) as resp:
            assert resp.status_code == 200
            ctype = resp.headers["content-type"]
            body = "".join(resp.iter_text())
        return ctype, _events(body)
    finally:
        for p in reversed(stack):
            p.stop()


def test_deltas_stream_then_a_terminal_done():
    ctype, events = _run(_FakeBrain())
    assert ctype.startswith("text/event-stream")

    names = [n for n, _ in events]
    assert names == ["delta", "delta", "delta", "done"], names

    import json

    texts = [json.loads(d)["text"] for n, d in events if n == "delta"]
    assert texts == ANSWER_PIECES
    assert "".join(texts) == FULL_ANSWER

    done = json.loads(events[-1][1])
    assert done["assistant_message"]["content"] == FULL_ANSWER
    assert done["assistant_message"]["intent"] == "portfolio_query"
    assert done["user_message"]["role"] == ChatMessageRole.user.value


def test_done_is_authoritative_when_it_differs_from_the_deltas():
    """The formatter can discard a truncated response and substitute a fallback.
    The client must be able to see that the final text is NOT the streamed text."""
    import json

    _, events = _run(_FakeBrain(final_text="A shorter fallback brief."))
    texts = [json.loads(d)["text"] for n, d in events if n == "delta"]
    done = json.loads(events[-1][1])

    assert "".join(texts) == FULL_ANSWER
    assert done["assistant_message"]["content"] == "A shorter fallback brief."


def test_a_failed_turn_emits_error_and_still_terminates():
    """A consumer must never hang on a turn that blew up."""
    _, events = _run(_FakeBrain(raise_exc=RuntimeError("boom")))
    names = [n for n, _ in events]
    assert names[-1] == "error"
    assert "done" not in names


@pytest.mark.parametrize("header", ["cache-control", "x-accel-buffering"])
def test_anti_buffering_headers_are_set(header):
    """Without these, a proxy can buffer the whole stream and defeat the point."""
    client, stack = _client(_FakeBrain())
    for p in stack:
        p.start()
    try:
        with client.stream(
            "POST",
            f"/chat/sessions/{SESSION_ID}/messages/stream",
            json={"content": "hi"},
        ) as resp:
            assert header in resp.headers
            resp.read()
    finally:
        for p in reversed(stack):
            p.stop()


def test_the_streaming_route_stores_the_turns_cta_on_the_assistant_row():
    """The write half of the CTA round trip, on the endpoint the app actually
    calls. Without this, dropping `cta=` from the streaming handler is silent:
    the pill still renders live and only goes missing after a reload."""
    brain = _FakeBrain(pill=True)
    db = _FakeDB()
    client, stack = _client(brain, db=db)
    with ExitStack() as es:
        for p in stack:
            es.enter_context(p)
        resp = client.post(
            f"/chat/sessions/{SESSION_ID}/messages/stream",
            json={"content": "what preferences have I set?"},
        )
    assert resp.status_code == 200

    assistant = [
        o for o in db.added if getattr(o, "role", None) == ChatMessageRole.assistant
    ]
    assert assistant, "no assistant row was persisted"
    assert assistant[0].cta == CTA_PREFERENCES
