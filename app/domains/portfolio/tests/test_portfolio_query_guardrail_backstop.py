"""An out-of-scope answer must never reach the customer.

The guardrail is not prompt-only. The model may write an out-of-scope reply into
`answer` and only then decide the question was Path X — so when
`guardrail_triggered` is set we discard `answer` outright and send the redirect.
This used to be a pydantic validator on PortfolioQueryResponse; the reply now
comes from the shared answer formatter, so the backstop lives beside it.
"""

from __future__ import annotations

from portfolio_query.models import _DEFAULT_REDIRECT

from app.domains.portfolio.services.portfolio_query_service import (
    _apply_guardrail_backstop,
)


def test_in_scope_answer_passes_through():
    out = _apply_guardrail_backstop(
        "You hold ₹23.61 lakh across 4 funds.",
        {"guardrail_triggered": False, "path": "P"},
    )
    assert out == "You hold ₹23.61 lakh across 4 funds."


def test_triggered_guardrail_discards_the_answer_even_when_populated():
    """The whole point: a populated `answer` must NOT win over the redirect."""
    out = _apply_guardrail_backstop(
        "Yes, sell your HDFC fund and buy the Axis one.",   # what we must never send
        {"guardrail_triggered": True, "redirect_message": "I can't advise on buy/sell calls."},
    )
    assert out == "I can't advise on buy/sell calls."
    assert "sell your HDFC" not in out


def test_triggered_guardrail_with_no_redirect_falls_back_to_the_canned_one():
    out = _apply_guardrail_backstop(
        "Yes, sell everything.",
        {"guardrail_triggered": True, "redirect_message": None},
    )
    assert out == _DEFAULT_REDIRECT


def test_blank_redirect_is_treated_as_missing():
    out = _apply_guardrail_backstop("x", {"guardrail_triggered": True, "redirect_message": "   "})
    assert out == _DEFAULT_REDIRECT


def test_empty_answer_without_a_guardrail_is_still_a_failure_reply():
    """Formatter returned "" but nothing flagged it — don't ship an empty message."""
    out = _apply_guardrail_backstop("", {"guardrail_triggered": False})
    assert out.strip()


def test_missing_extras_are_treated_as_in_scope():
    """A malformed tool call must not silently redirect a legitimate question."""
    out = _apply_guardrail_backstop("You hold 4 funds.", {})
    assert out == "You hold 4 funds."
