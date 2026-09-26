"""Tests for the shared relay helper (format_relay_or_canned in formatter.py).

It routes a resolved canned boundary/redirect/gate message through the shared
formatter as facts_pack={"boundary_message": message}, and falls back to that
exact message if the formatter fails.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.domains.ai_engine.answer_formatter import formatter as fmt_mod


def test_relay_passes_message_and_args():
    captured = {}

    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        captured.update(
            facts_pack=facts_pack, body_prompt=body_prompt, module_name=module_name,
            action_mode=action_mode, profile=profile, fallback=build_fallback(),
        )
        return "TAILORED"

    ctx = SimpleNamespace(user_ctx=SimpleNamespace(first_name="Asha"))
    with patch.object(fmt_mod, "format_with_telemetry", new=fake_fmt):
        out = asyncio.run(fmt_mod.format_relay_or_canned(
            ctx=ctx, module_name="rebalancing", message="GO TO PROFILE",
        ))
    assert out == "TAILORED"
    assert captured["facts_pack"] == {"boundary_message": "GO TO PROFILE"}
    assert captured["module_name"] == "rebalancing"
    assert captured["action_mode"] == "redirect"
    assert captured["profile"] == {"first_name": "Asha"}
    assert captured["fallback"] == "GO TO PROFILE"
    assert captured["body_prompt"] == fmt_mod._RELAY_BODY

def test_relay_returns_fallback_on_formatter_failure():
    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        return build_fallback()  # simulate FormatterFailure -> fallback closure

    ctx = SimpleNamespace(user_ctx=SimpleNamespace(first_name=None))
    with patch.object(fmt_mod, "format_with_telemetry", new=fake_fmt):
        out = asyncio.run(fmt_mod.format_relay_or_canned(
            ctx=ctx, module_name="goal_planning", message="NEED DOB",
        ))
    assert out == "NEED DOB"


def test_relay_is_exported_from_package():
    from app.domains.ai_engine.answer_formatter import format_relay_or_canned
    assert format_relay_or_canned is fmt_mod.format_relay_or_canned
