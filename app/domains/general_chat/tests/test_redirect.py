"""Tests for the out_of_scope / stock_advice redirect handler.

should_tailor encodes the tailor-vs-canned matrix; format_redirect_or_canned
returns the sub-reason-specific canned line verbatim (no LLM) for the sensitive
sub-reasons, runs the shared formatter for the tailored ones, and falls back to
the canned line if the formatter fails.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_should_tailor_matrix():
    from app.domains.general_chat.services import general_chat_engine as eng
    S = eng.OutOfScopeSubreason
    assert eng.should_tailor("stock_advice", None) is True
    assert eng.should_tailor("out_of_scope", S.OFF_TOPIC) is True
    assert eng.should_tailor("out_of_scope", S.OTHER) is True
    for sr in (S.GIBBERISH, S.IDENTITY_OR_META, S.SECURITY_OR_CREDENTIALS, S.CHAT_SUMMARY):
        assert eng.should_tailor("out_of_scope", sr) is False


def test_canned_subreasons_return_specific_line_without_llm():
    from app.domains.general_chat.services import general_chat_engine as eng
    S = eng.OutOfScopeSubreason
    with patch.object(eng, "format_with_telemetry", new=AsyncMock()) as mock_fmt:
        for sr in (S.GIBBERISH, S.IDENTITY_OR_META, S.SECURITY_OR_CREDENTIALS, S.CHAT_SUMMARY):
            intent = SimpleNamespace(
                name="out_of_scope",
                raw=SimpleNamespace(
                    out_of_scope_message=eng.OUT_OF_SCOPE_MESSAGE,
                    out_of_scope_subreason=sr,
                ),
            )
            result = asyncio.run(eng.format_redirect_or_canned(ctx=None, intent=intent))
            assert result == eng._OOS_REPLIES_BY_SUBREASON[sr]
        mock_fmt.assert_not_called()


def test_tailored_calls_formatter_with_redirect_mode():
    from app.domains.general_chat.services import general_chat_engine as eng
    captured = {}

    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        captured.update(
            facts_pack=facts_pack, module_name=module_name,
            action_mode=action_mode, profile=profile,
        )
        return "TAILORED REPLY"

    intent = SimpleNamespace(
        name="out_of_scope",
        raw=SimpleNamespace(
            out_of_scope_message=eng.OUT_OF_SCOPE_MESSAGE,
            out_of_scope_subreason=eng.OutOfScopeSubreason.OFF_TOPIC,
        ),
    )
    ctx = SimpleNamespace(user_ctx=SimpleNamespace(first_name="Asha"))
    with patch.object(eng, "format_with_telemetry", new=fake_fmt):
        result = asyncio.run(eng.format_redirect_or_canned(ctx=ctx, intent=intent))
    assert result == "TAILORED REPLY"
    assert captured["action_mode"] == "redirect"
    assert captured["module_name"] == "out_of_scope"
    assert set(captured["facts_pack"]) == {"boundary_message"}
    assert captured["facts_pack"]["boundary_message"] == \
        eng._OOS_REPLIES_BY_SUBREASON[eng.OutOfScopeSubreason.OFF_TOPIC]
    assert captured["profile"] == {"first_name": "Asha"}


def test_tailored_falls_back_to_canned_on_formatter_failure():
    from app.domains.general_chat.services import general_chat_engine as eng

    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        return build_fallback()  # simulate FormatterFailure -> fallback closure

    intent = SimpleNamespace(
        name="stock_advice",
        raw=SimpleNamespace(out_of_scope_message="STOCK_CANNED", out_of_scope_subreason=None),
    )
    ctx = SimpleNamespace(user_ctx=SimpleNamespace(first_name=None))
    with patch.object(eng, "format_with_telemetry", new=fake_fmt):
        result = asyncio.run(eng.format_redirect_or_canned(ctx=ctx, intent=intent))
    assert result == "STOCK_CANNED"
