"""build_history_block marks long gaps between turns (incident 2026-07-25).

Sessions here are long-lived threads, not single sittings — the incident session
ran 07-03 to 07-25. Without a time signal every prior turn reads as if it were
asked moments ago, so a fortnight-old question looks like live context. The
2026-07-25 turn inherited a 13-day-old goal question that way.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domains.ai_engine.common import build_history_block, with_gap_notes

T0 = datetime(2026, 7, 12, 9, 3, 21, tzinfo=timezone.utc)


def _turn(question: str, answer: str, at: datetime) -> list[dict]:
    return [
        {"role": "user", "content": question, "intent": None, "asked_at": at},
        {"role": "assistant", "content": answer, "intent": "x", "asked_at": at},
    ]


def test_long_gap_between_turns_is_marked():
    history = _turn("old question", "old answer", T0) + _turn(
        "new question", "new answer", T0 + timedelta(days=13)
    )

    block = build_history_block(history)

    assert "13 days later" in block
    # The marker must separate the turns, not trail the block.
    assert block.index("old answer") < block.index("13 days later")
    assert block.index("13 days later") < block.index("new question")


def test_gap_is_rounded_not_floored():
    """A 47-hour break is nearly two days; flooring would call it one."""
    history = _turn("old", "answer", T0) + _turn(
        "new", "answer", T0 + timedelta(hours=47)
    )

    assert "2 days later" in build_history_block(history)


def test_turns_in_one_sitting_are_not_marked():
    history = _turn("first", "answer one", T0) + _turn(
        "second", "answer two", T0 + timedelta(minutes=3)
    )

    block = build_history_block(history)

    assert "later" not in block


def test_gap_note_is_folded_into_the_resumed_turn():
    """For consumers that map history into their own {role, content} objects.

    The intent classifier and portfolio_query both rebuild history as message
    models with no timestamp field, so a separate marker line has nowhere to
    live — the note has to ride inside the content.
    """
    history = _turn("old question", "old answer", T0) + _turn(
        "new question", "new answer", T0 + timedelta(days=13)
    )

    annotated = with_gap_notes(history)

    assert [m["content"] for m in annotated] == [
        "old question",
        "old answer",
        "[13 days later] new question",
        "new answer",
    ]


def test_gap_note_leaves_a_single_sitting_alone():
    history = _turn("first", "answer one", T0) + _turn(
        "second", "answer two", T0 + timedelta(minutes=3)
    )

    assert with_gap_notes(history) == history


def test_gap_note_passes_through_history_without_timestamps():
    history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]

    assert with_gap_notes(history) == history


def test_history_without_timestamps_still_renders():
    """Callers that hand-build history (debug routers, tests) omit asked_at."""
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]

    block = build_history_block(history)

    assert "Customer: q" in block
    assert "PI: a" in block
