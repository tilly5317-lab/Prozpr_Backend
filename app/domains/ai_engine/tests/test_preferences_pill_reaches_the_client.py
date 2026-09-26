"""The preferences pill must survive the WHOLE chain, not just the handler.

Review finding (2026-09-17, critical): every module handler set
`show_preferences_pill=True` correctly, but two of the three module services
dropped it when building `ModuleOutput` — so `PREFERENCE_REDIRECT_MESSAGE`
told the customer to "open it below" with nothing below. The existing tests all
asserted on `ChatHandlerResult`, one layer BELOW where the flag died, so they
stayed green.

These tests assert at the module-service boundary — the first place the flag can
be lost — for all three producers.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


def _ctx():
    from app.domains.rebalancing.tests.detector_ctx import make_detector_ctx

    return make_detector_ctx(
        "I want more equity",
        user_ctx=SimpleNamespace(id=uuid.uuid4(), first_name="A"),
    )


def _handler_result(module: str):
    """What each handler returns on a preference-redirect turn."""
    from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult

    return ChatHandlerResult(text="pointer", show_preferences_pill=True)


@pytest.mark.parametrize(
    "module,service_path,fn",
    [
        (
            "rebalancing",
            "app.domains.rebalancing.services.rebalancing_module_service",
            "run",
        ),
        (
            "asset_allocation",
            "app.domains.asset_allocation.services.asset_allocation_module_service",
            "run",
        ),
        (
            "additional_investment",
            "app.domains.additional_investment.services.additional_investment_module_service",
            "run",
        ),
    ],
)
async def test_the_module_service_carries_the_pill(module, service_path, fn, monkeypatch):
    """A dropped flag here is invisible to every handler-level test."""
    import importlib

    svc = importlib.import_module(service_path)

    async def _fake_dispatch(name, ctx):
        assert name == module
        return _handler_result(module)

    monkeypatch.setattr(
        "app.domains.ai_engine.chat_dispatcher.dispatch_chat", _fake_dispatch
    )

    out = await getattr(svc, fn)(None, _ctx(), {})

    assert out.show_preferences_pill is True, (
        f"{module} drops show_preferences_pill: the reply says 'open it below' "
        "and the client renders no pill"
    )


def test_the_client_facing_flags_exist_at_every_hop():
    """Guard the whole chain by shape: a flag present on one hop but missing
    from the next is silently lost, which is exactly how the pill bug shipped."""
    import dataclasses

    from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult
    from app.domains.ai_engine.types import ModuleOutput
    from app.domains.ai_engine.chat_types import ChatBrainResult
    from app.domains.chat.schemas.chat import ChatSendMessageResponse

    def fields(t):
        if dataclasses.is_dataclass(t):
            return {f.name for f in dataclasses.fields(t)}
        return set(getattr(t, "model_fields", {}))

    hops = [
        ("ChatHandlerResult", ChatHandlerResult),
        ("ModuleOutput", ModuleOutput),
        ("ChatBrainResult", ChatBrainResult),
        ("ChatSendMessageResponse", ChatSendMessageResponse),
    ]
    for flag in ("show_preferences_pill", "has_candidate_preference"):
        for name, t in hops:
            assert flag in fields(t), (
                f"{flag} is missing from {name} — it cannot reach the client"
            )
