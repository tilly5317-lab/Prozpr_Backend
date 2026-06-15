"""Unit tests for the shared persona builder."""
import pytest
from persona import build_system_prompt, PI_IDENTITY, FORMAT_PROFILES


def test_chat_prompt_has_identity_money_and_question_opening():
    s = build_system_prompt("BODY", format_profile="chat")
    assert "You are PI" in s
    assert "_indian" in s                      # money rule present
    assert "restating" in s.lower()            # question-opening present
    assert "next step only when" in s.lower()  # conditional next-step guidance present
    assert "BODY" in s                         # body appended
    # Prohibitions the formatter test also relies on:
    low = s.lower()
    assert "don't invent or recommend mutual funds" in low
    assert "never quote isins" in low
    assert "never invent numbers" in low


def test_plain_profile_forbids_block_markdown_and_can_drop_question():
    s = build_system_prompt("BODY", format_profile="plain", question_aware=False)
    assert "You are PI" in s
    low = s.lower()
    assert "do not use tables" in low or "no tables" in low
    assert "restating" not in low              # question_aware=False omits it
    assert "next step only when" not in low    # next-step is question-aware only


def test_document_profile_omits_question_opening():
    s = build_system_prompt("BODY", format_profile="document", question_aware=False)
    assert "restating" not in s.lower()
    assert "next step only when" not in s.lower()


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        build_system_prompt("x", format_profile="nope")


def test_never_contains_tilly():
    for p in FORMAT_PROFILES:
        assert "tilly" not in build_system_prompt("", format_profile=p, question_aware=False).lower()
