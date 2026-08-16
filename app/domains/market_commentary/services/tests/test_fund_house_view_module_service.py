"""The view module is a plain file read that degrades gracefully."""
from __future__ import annotations

import asyncio

from app.domains.market_commentary.services import fund_house_view_module_service as svc


def _run():
    return asyncio.run(svc.run(turn=None, ctx=None, prior={}))


def test_returns_text_when_file_present(tmp_path, monkeypatch):
    f = tmp_path / "fund_house_commentry.md"
    f.write_text("PROZPR VIEW TEXT", encoding="utf-8")
    monkeypatch.setattr(svc, "_VIEW_PATH", f)

    assert _run().payload == "PROZPR VIEW TEXT"


def test_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_VIEW_PATH", tmp_path / "absent.md")

    assert _run().payload is None


def test_returns_none_when_file_empty(tmp_path, monkeypatch):
    f = tmp_path / "fund_house_commentry.md"
    f.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(svc, "_VIEW_PATH", f)

    assert _run().payload is None
