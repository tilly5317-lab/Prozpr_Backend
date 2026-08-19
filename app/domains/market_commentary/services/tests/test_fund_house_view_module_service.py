"""The market view module delegates to the shared house_view loader (prozpr_only=False)."""
from __future__ import annotations

import asyncio

from app.domains.market_commentary.services import fund_house_view_module_service as svc


def _run():
    return asyncio.run(svc.run(turn=None, ctx=None, prior={}))


def test_returns_full_view_when_available(monkeypatch):
    captured = {}

    def fake_loader(*, prozpr_only):
        captured["prozpr_only"] = prozpr_only
        return "FULL MULTI-HOUSE VIEW"

    monkeypatch.setattr(svc, "load_house_view", fake_loader)
    assert _run().payload == "FULL MULTI-HOUSE VIEW"
    assert captured["prozpr_only"] is False        # market gets every house


def test_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(svc, "load_house_view", lambda *, prozpr_only: None)
    assert _run().payload is None
