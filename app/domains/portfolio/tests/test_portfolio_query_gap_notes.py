"""portfolio_query must see how old the conversation it is reading is.

This is the agent that failed on 2026-07-25: it read a fortnight-old goal
question as live context and answered that instead of "Review my portfolio".
A history turn carries only role and content, so the age note rides inside
the content.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domains.portfolio.services.portfolio_query_service import _build_history

T0 = datetime(2026, 7, 12, 9, 3, 21, tzinfo=timezone.utc)


def _turn(question: str, answer: str, at: datetime) -> list[dict]:
    return [
        {"role": "user", "content": question, "intent": None, "asked_at": at},
        {"role": "assistant", "content": answer, "intent": "goal_planning", "asked_at": at},
    ]


def test_resumed_conversation_is_marked():
    history = _turn("old question", "old answer", T0) + _turn(
        "Wil I be able to meet my goals?", "Fully funded.", T0 + timedelta(days=13)
    )

    turns = _build_history(history)

    assert [t["content"] for t in turns] == [
        "old question",
        "old answer",
        "[13 days later] Wil I be able to meet my goals?",
        "Fully funded.",
    ]


def test_single_sitting_is_not_marked():
    history = _turn("first", "answer one", T0) + _turn(
        "second", "answer two", T0 + timedelta(minutes=3)
    )

    turns = _build_history(history)

    assert all("later]" not in t["content"] for t in turns)
