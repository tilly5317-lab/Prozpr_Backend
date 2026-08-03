"""Unit tests for the shared persona builder."""

import pytest
from persona import build_system_prompt, FORMAT_PROFILES


def test_chat_prompt_has_identity_money_and_question_opening():
    s = build_system_prompt("BODY", format_profile="chat")
    assert "You are PI" in s
    assert "_indian" in s  # money rule present
    assert "restating" in s.lower()  # question-opening present
    assert "next step only when" in s.lower()  # conditional next-step guidance present
    assert "BODY" in s  # body appended
    # Prohibitions the formatter test also relies on:
    low = s.lower()
    assert "don't invent or recommend mutual funds" in low
    assert "never quote isins" in low
    assert "never invent numbers" in low


def test_chat_profile_carries_the_no_internal_plumbing_rules():
    """Every chat surface inherits these — they used to be copied per-prompt."""
    s = build_system_prompt("BODY", format_profile="chat")
    assert "Never name an internal section" in s
    assert "in my data" in s
    assert "name the MISSING THING in the customer's own terms" in s


def test_non_chat_profiles_do_not_carry_them():
    for profile in ("plain", "document"):
        s = build_system_prompt("BODY", format_profile=profile, question_aware=False)
        assert "Never name an internal section" not in s


def test_plain_profile_forbids_block_markdown_and_can_drop_question():
    s = build_system_prompt("BODY", format_profile="plain", question_aware=False)
    assert "You are PI" in s
    low = s.lower()
    assert "do not use tables" in low or "no tables" in low
    assert "restating" not in low  # question_aware=False omits it
    assert "next step only when" not in low  # next-step is question-aware only


def test_document_profile_omits_question_opening():
    s = build_system_prompt("BODY", format_profile="document", question_aware=False)
    assert "restating" not in s.lower()
    assert "next step only when" not in s.lower()


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        build_system_prompt("x", format_profile="nope")


def test_never_contains_tilly():
    for p in FORMAT_PROFILES:
        assert (
            "tilly"
            not in build_system_prompt(
                "", format_profile=p, question_aware=False
            ).lower()
        )
