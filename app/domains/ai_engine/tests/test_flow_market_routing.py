"""flow_market loads factual and/or view per ctx.tools_needed."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.domains.market_commentary.services.market_commentary_module_service as factual_mod
import app.domains.market_commentary.services.fund_house_view_module_service as view_mod
import app.domains.general_chat.services.general_chat_module_service as gc_mod
from app.domains.ai_engine.services import flow
from app.domains.ai_engine.types import ModuleOutput


def _wire(monkeypatch):
    captured = {}

    async def fake_factual(turn, ctx, prior):
        captured["factual_called"] = True
        return ModuleOutput(payload="FACTUAL_DOC")

    async def fake_view(turn, ctx, prior):
        captured["view_called"] = True
        return ModuleOutput(payload="VIEW_DOC")

    async def fake_gc(turn, ctx, prior):
        captured["combined"] = prior[flow.AIModule.MARKET_COMMENTARY.value].payload
        return ModuleOutput(text="REPLY")

    monkeypatch.setattr(factual_mod, "run", fake_factual)
    monkeypatch.setattr(view_mod, "run", fake_view)
    monkeypatch.setattr(gc_mod, "run", fake_gc)
    monkeypatch.setattr(flow, "_think", lambda *a, **k: None)
    return captured


def _ctx(*tools):
    return SimpleNamespace(tools_needed=tuple(tools))


def test_factual_only(monkeypatch):
    cap = _wire(monkeypatch)
    asyncio.run(flow.flow_market(SimpleNamespace(), _ctx("market_commentary")))
    assert cap.get("factual_called") and not cap.get("view_called")
    assert "FACTUAL_DOC" in cap["combined"] and "VIEW_DOC" not in cap["combined"]


def test_view_only(monkeypatch):
    cap = _wire(monkeypatch)
    asyncio.run(flow.flow_market(SimpleNamespace(), _ctx("fund_house_view")))
    assert cap.get("view_called") and not cap.get("factual_called")
    assert "VIEW_DOC" in cap["combined"] and "FACTUAL_DOC" not in cap["combined"]


def test_both(monkeypatch):
    cap = _wire(monkeypatch)
    asyncio.run(flow.flow_market(SimpleNamespace(), _ctx("market_commentary", "fund_house_view")))
    assert cap.get("factual_called") and cap.get("view_called")
    assert "FACTUAL_DOC" in cap["combined"] and "VIEW_DOC" in cap["combined"]


def test_empty_defaults_to_factual(monkeypatch):
    cap = _wire(monkeypatch)
    asyncio.run(flow.flow_market(SimpleNamespace(), _ctx()))
    assert cap.get("factual_called") and not cap.get("view_called")
