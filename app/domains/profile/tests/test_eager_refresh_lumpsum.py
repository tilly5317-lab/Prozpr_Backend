"""S4: `_eager_refresh` recomputes a prior lump-sum plan on preference save,
mirroring the existing SIP block."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.domains.profile.services import preference_save_service as svc


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _FakeDB:
    """Returns the same run for any query; records commits/rollbacks."""

    def __init__(self, row):
        self._row = row
        self.commits = 0

    async def execute(self, stmt):
        return _FakeResult(self._row)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


async def test_eager_refresh_recomputes_both_sip_and_lumpsum(monkeypatch):
    cadences: list = []

    async def fake_rebal(*args, **kwargs):
        return None

    async def fake_ainv(user, why, **kwargs):
        cadences.append(kwargs.get("cadence"))
        return SimpleNamespace()

    monkeypatch.setattr(
        "app.domains.rebalancing.services.rebal_engine.service.compute_rebalancing_result",
        fake_rebal,
    )
    monkeypatch.setattr(
        "app.domains.additional_investment.services.ainv_engine.service."
        "compute_additional_investment_result",
        fake_ainv,
    )

    from app.domains.additional_investment.models.additional_investment_run import Cadence

    db = _FakeDB(SimpleNamespace(deploy_amount_inr=50000.0))
    await svc._eager_refresh(db, SimpleNamespace(id=uuid.uuid4()))

    assert Cadence.SIP_MONTHLY in cadences
    assert Cadence.LUMPSUM in cadences
