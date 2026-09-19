"""Regression tests for the general_chat None-classification crash.

flow_market / flow_general_chat never seed prior[INTENT_CLASSIFIER], so
general_chat_module_service.run() legitimately calls the engine with
classification=None. The engine must tolerate that everywhere — both the
early out_of_scope / stock_advice guards AND the LLM user-prompt build (which
embedded `classification.intent.value`). Before the fix these raised
``AttributeError: 'NoneType' object has no attribute 'intent'``.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _settings_with_key(key: str):
    s = MagicMock()
    s.get_anthropic_general_chat_key.return_value = key
    return s


def test_none_classification_no_api_key_path():
    """No key → graceful message, no crash on the early classification guards."""
    from app.domains.general_chat.services import general_chat_engine as eng
    with patch.object(eng, "get_settings", return_value=_settings_with_key("")):
        result = asyncio.run(eng.generate_general_chat_response(
            user_question="What's the current RBI repo rate?",
            classification=None,
            market_commentary=None,
            conversation_history=[],
            client_context=None,
        ))
    assert isinstance(result, str) and result.strip()


def _ctx():
    import types
    return types.SimpleNamespace(
        db=None, effective_user_id=1, session_id="s1",
        user_question="What's the current RBI repo rate?",
        conversation_history=[], user_ctx=types.SimpleNamespace(first_name="A"),
    )


def test_none_classification_full_llm_path():
    """With a key, the function runs the research pass and hands the digest to the
    shared formatter. Mock both so no network call happens; assert the composed
    answer comes back without crashing on classification=None."""
    from app.domains.general_chat.services import general_chat_engine as eng

    research_resp = MagicMock()
    research_resp.content = "RBI repo rate research digest."
    bound = MagicMock()
    bound.ainvoke = AsyncMock(return_value=research_resp)
    fake_chat = MagicMock()
    fake_chat.bind_tools.return_value = bound

    captured = {}

    async def fake_format(**kwargs):
        captured.update(kwargs)
        return "The RBI repo rate is **6.5%** per our daily snapshot."

    with patch.object(eng, "get_settings", return_value=_settings_with_key("sk-fake")), \
         patch.object(eng, "ChatAnthropic", return_value=fake_chat), \
         patch.object(eng, "format_with_telemetry", fake_format):
        result = asyncio.run(eng.generate_general_chat_response(
            user_question="What's the current RBI repo rate?",
            classification=None,
            market_commentary="RBI repo rate is 6.5%.",
            conversation_history=[],
            client_context=None,
            ctx=_ctx(),
        ))
    assert "6.5%" in result  # reached prompt-build + compose with None classification, no crash
    assert captured["module_name"] == "general_chat"
    assert captured["action_mode"] == "narrate"
    # The digest is the compose pass's only market data — the 7K commentary is not re-sent.
    assert captured["facts_pack"]["research_digest"] == "RBI repo rate research digest."
    assert "classifier_intent" not in captured["facts_pack"]   # None classification
    assert "RBI repo rate is 6.5%." not in str(captured["facts_pack"])


def test_commentary_cap_fits_factual_plus_view():
    """flow_market concatenates factual (~15K) + fund-house view (~90K); the
    research-pass truncation must fit both or it drops whole asset classes."""
    from app.domains.general_chat.services import general_chat_engine as gce

    assert gce._MAX_COMMENTARY_CHARS >= 105_000


def test_research_prompt_cites_houses_as_sources():
    """View answers surface named fund houses as research sources, led by Prozpr's stance
    (confidence-building) — but never as recommendations to the customer."""
    from app.domains.general_chat.services import general_chat_engine as gce

    p = gce._RESEARCH_SYSTEM_PROMPT
    assert "PROZPR HOUSE VIEW" in p
    assert "ICICI" in p  # houses named as sources, not scrubbed
    assert "research" in p.lower()
    assert "never as recommendations" in p.lower()
