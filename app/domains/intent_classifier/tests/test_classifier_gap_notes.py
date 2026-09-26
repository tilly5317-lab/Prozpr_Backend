"""The classifier must see how old the conversation it is reading is.

Sessions are long-lived threads. On 2026-07-25 a fortnight-old goal question sat
at the end of the history and read as live context. The classifier's message
model carries only role and content, so the age note rides inside the content.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.domains.intent_classifier.services.intent_classifier_engine as engine

T0 = datetime(2026, 7, 12, 9, 3, 21, tzinfo=timezone.utc)


def _turn(question: str, answer: str, at: datetime) -> list[dict]:
    return [
        {"role": "user", "content": question, "intent": None, "asked_at": at},
        {"role": "assistant", "content": answer, "intent": "goal_planning", "asked_at": at},
    ]


class _FakeClassifier:
    """Captures what the real Anthropic classifier would have been sent."""

    def __init__(self):
        self.seen: list = []

    async def aclassify(self, inp):
        self.seen = list(inp.conversation_history)
        return engine.ClassificationResult(
            intent=engine.Intent.PORTFOLIO_QUERY,
            confidence=0.9,
            reasoning="stub",
            out_of_scope_message=None,
        )


@pytest.mark.asyncio
async def test_classifier_history_carries_the_gap_note(monkeypatch):
    fake = _FakeClassifier()
    monkeypatch.setattr(engine, "_get_classifier", lambda: fake)
    history = _turn("Wil I be able to meet my goals?", "Fully funded.", T0) + _turn(
        "earlier", "earlier reply", T0 - timedelta(minutes=2)
    )

    await engine.classify_user_message("Review my portfolio", history)

    contents = [m.content for m in fake.seen]
    assert any(c.startswith("[13 days later]") for c in contents) is False, (
        "no gap in this fixture — guards against a false positive"
    )


@pytest.mark.asyncio
async def test_gap_note_reaches_the_primary_classifier(monkeypatch):
    fake = _FakeClassifier()
    monkeypatch.setattr(engine, "_get_classifier", lambda: fake)
    history = _turn("old question", "old answer", T0) + _turn(
        "Wil I be able to meet my goals?", "Fully funded.", T0 + timedelta(days=13)
    )

    await engine.classify_user_message("Review my portfolio", history)

    contents = [m.content for m in fake.seen]
    assert "[13 days later] Wil I be able to meet my goals?" in contents
