"""Prior answers enter this agent's context as excerpts, not in full.

A goal-planning reply runs to ~14,000 characters (30-year cashflow table). Six
of those at the shared 24,000-char cap swamp the prompt: measured live, the
agent answered the PREVIOUS turn's thread verbatim instead of the question
asked. The same question with no history, or with only the last turn, was
answered correctly — so volume was the cause, not recency.

History here exists to resolve pronouns and shorthand. An excerpt does that;
the full table only adds noise.
"""

from __future__ import annotations

from app.domains.portfolio.services.portfolio_query_service import (
    _MAX_HISTORY_REPLY_CHARS,
    _build_history,
)


def test_long_prior_answer_is_excerpted():
    history = [
        {"role": "user", "content": "Wil I be able to meet my goals?"},
        {"role": "assistant", "content": "# Retirement\n" + ("table row | " * 4000)},
    ]

    turns = _build_history(history)

    assert len(turns[1]["content"]) <= _MAX_HISTORY_REPLY_CHARS + 20
    assert turns[1]["content"].startswith("# Retirement"), "the opening must survive"


def test_the_customers_own_words_are_never_truncated():
    """User messages are the questions themselves — short, and the part that matters."""
    question = "Wil I be able to meet my goals? " * 60

    turns = _build_history([{"role": "user", "content": question}])

    assert turns[0]["content"] == question


def test_short_replies_pass_through_untouched():
    history = [
        {"role": "user", "content": "Review my portfolio"},
        {"role": "assistant", "content": "You hold ₹23.61 lakh across 4 funds."},
    ]

    turns = _build_history(history)

    assert turns[1]["content"] == "You hold ₹23.61 lakh across 4 funds."
