"""S4 router: PUT /investment-preferences is wired to the screen save service."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domains.profile.routers import profile_router as pr
from app.domains.profile.schemas import ScreenPreferenceRequest, ScreenSaveResponse
from app.domains.profile.services import screen_preference_service as svc


async def test_put_maps_screen_error_to_422(monkeypatch):
    async def boom(db, user, class_mix, pins):
        raise svc.ScreenPreferenceError("Equity + Debt + Commodity must total 100%.")

    monkeypatch.setattr(svc, "save_screen_preference", boom)

    payload = ScreenPreferenceRequest(
        class_mix={"equity": 50, "debt": 40, "others": 10}, pins=[]
    )
    with pytest.raises(HTTPException) as ei:
        await pr.put_investment_preferences(
            payload, db=None, user_ctx=SimpleNamespace(id=uuid.uuid4())
        )
    assert ei.value.status_code == 422
    assert "total 100" in str(ei.value.detail)


async def test_put_forwards_mix_and_pins_and_returns_response(monkeypatch):
    async def ok(db, user, class_mix, pins):
        assert class_mix == {"equity": 72, "debt": 18, "others": 10}
        assert pins == [{"subgroup": "low_beta_equities", "pct_of_total": 25}]
        return ScreenSaveResponse(ok=True)

    monkeypatch.setattr(svc, "save_screen_preference", ok)

    payload = ScreenPreferenceRequest(
        class_mix={"equity": 72, "debt": 18, "others": 10},
        pins=[{"subgroup": "low_beta_equities", "pct_of_total": 25}],
    )
    resp = await pr.put_investment_preferences(
        payload, db=None, user_ctx=SimpleNamespace(id=uuid.uuid4())
    )
    assert resp.ok is True
