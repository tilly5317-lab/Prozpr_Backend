"""Persist an additional-investment engine result across the normalized
additional_investment_* tables. Fakes only: no real DB, no LLM."""

import uuid

import pytest

# Populate the full ORM mapper registry so configure_mappers() (run
# registry-wide on first instantiation below) can resolve every
# string-based relationship() target without a live DB.
from app import all_models  # noqa: F401

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from additional_investment.models import (  # noqa: E402
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
    TargetBucket,
    Cadence,
    FundBuy,
    RankedFund,
    SubgroupTarget,
)


class _FakePortfolio:
    def __init__(self):
        self.id = uuid.uuid4()


class _FakeSession:
    """Minimal AsyncSession stand-in: records add()s and assigns a uuid id
    to every flushed row whose id is still unset (the real INSERT default)."""

    def __init__(self):
        self.added = []
        self.flush_count = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


async def _fake_get_or_create_primary_portfolio(db, user_id):
    return _FakePortfolio()


def _sip_output():
    return AdditionalInvestmentOutput(
        target_bucket=TargetBucket.LONG_TERM,
        cadence=Cadence.SIP_MONTHLY,
        deploy_amount_inr=120000.0,
        deployed_inr=120000.0,
        undeployed_inr=0.0,
        per_subgroup_target=[
            SubgroupTarget(subgroup="low_beta_equities", ratio=0.6, target_inr=72000.0),
            SubgroupTarget(subgroup="high_beta_equities", ratio=0.4, target_inr=48000.0),
        ],
        buys=[
            FundBuy(
                recommended_fund="Fund A",
                isin="INF001",
                sub_category="Large Cap Fund",
                asset_subgroup="low_beta_equities",
                amount_inr=72000.0,
                monthly_amount_inr=6000.0,
                reason="rank-1 fresh buy",
            ),
            FundBuy(
                recommended_fund="Fund B",
                isin="INF002",
                sub_category="Mid Cap Fund",
                asset_subgroup="high_beta_equities",
                amount_inr=48000.0,
                monthly_amount_inr=4000.0,
                reason="rank-1 fresh buy",
            ),
        ],
    )


def _sip_request():
    """Engine input whose ranked_funds carry the rank + scheme_code the persist
    service joins onto each buy by isin."""
    return AdditionalInvestmentInput(
        deploy_amount_inr=120000.0,
        cadence=Cadence.SIP_MONTHLY,
        subgroups=[],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                rank=1,
                isin="INF001",
                scheme_code="SC001",
                recommended_fund="Fund A",
            ),
            RankedFund(
                asset_subgroup="high_beta_equities",
                sub_category="Mid Cap Fund",
                rank=2,
                isin="INF002",
                scheme_code="SC002",
                recommended_fund="Fund B",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_persist_writes_run_targets_and_buys(monkeypatch):
    from app.domains.additional_investment.services import (
        additional_investment_persist_service as svc,
    )

    monkeypatch.setattr(
        svc, "get_or_create_primary_portfolio", _fake_get_or_create_primary_portfolio
    )

    from app.domains.additional_investment.models import (
        AdditionalInvestmentBuy,
        AdditionalInvestmentRun,
        AdditionalInvestmentTarget,
    )

    db = _FakeSession()
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()

    run_id = await svc.persist_additional_investment_recommendation(
        db,
        user_id,
        _sip_output(),
        source_allocation_run_id=source_id,
        chat_session_id=None,
        user_question="invest 10k monthly",
        request=_sip_request(),
    )

    assert isinstance(run_id, uuid.UUID)

    runs = [o for o in db.added if isinstance(o, AdditionalInvestmentRun)]
    targets = [o for o in db.added if isinstance(o, AdditionalInvestmentTarget)]
    buys = [o for o in db.added if isinstance(o, AdditionalInvestmentBuy)]

    assert len(runs) == 1
    run = runs[0]
    assert run.id == run_id
    assert run.user_id == user_id
    assert run.source_allocation_run_id == source_id
    assert run.target_bucket == "long_term"   # enum .value persisted as String
    assert run.cadence == "sip_monthly"
    assert run.deploy_amount_inr == 120000.0  # float straight into Numeric
    assert run.deployed_inr == 120000.0
    assert run.undeployed_inr == 0.0
    assert run.user_question == "invest 10k monthly"
    assert run.engine_version == "ainv-3.3.0"  # stamped from AINV_ENGINE_VERSION

    # N targets, all parented to the flushed run.
    assert len(targets) == 2
    assert {t.subgroup for t in targets} == {"low_beta_equities", "high_beta_equities"}
    assert all(t.run_id == run_id for t in targets)
    assert {round(t.ratio, 2) for t in targets} == {0.6, 0.4}

    # M buys, parented to the run; monthly set because cadence == sip_monthly.
    assert len(buys) == 2
    assert all(b.run_id == run_id for b in buys)
    assert {b.isin for b in buys} == {"INF001", "INF002"}
    assert all(b.monthly_amount_inr is not None for b in buys)

    # rank + scheme_code recovered from request.ranked_funds by isin join.
    buys_by_isin = {b.isin: b for b in buys}
    assert buys_by_isin["INF001"].rank == 1
    assert buys_by_isin["INF001"].scheme_code == "SC001"
    assert buys_by_isin["INF002"].rank == 2
    assert buys_by_isin["INF002"].scheme_code == "SC002"

    # Two flushes: one to obtain run.id, one for the children.
    assert db.flush_count == 2


@pytest.mark.asyncio
async def test_persist_lumpsum_buys_have_no_monthly_amount(monkeypatch):
    from app.domains.additional_investment.services import (
        additional_investment_persist_service as svc,
    )

    monkeypatch.setattr(
        svc, "get_or_create_primary_portfolio", _fake_get_or_create_primary_portfolio
    )

    from app.domains.additional_investment.models import AdditionalInvestmentBuy

    output = AdditionalInvestmentOutput(
        target_bucket=TargetBucket.MEDIUM_TERM,
        cadence=Cadence.LUMPSUM,
        deploy_amount_inr=50000.0,
        deployed_inr=50000.0,
        undeployed_inr=0.0,
        per_subgroup_target=[
            SubgroupTarget(subgroup="low_beta_equities", ratio=1.0, target_inr=50000.0),
        ],
        buys=[
            FundBuy(
                recommended_fund="Fund A",
                isin="INF001",
                sub_category="Large Cap Fund",
                asset_subgroup="low_beta_equities",
                amount_inr=50000.0,
                reason="rank-1 fresh buy",
            ),
        ],
    )

    request = AdditionalInvestmentInput(
        deploy_amount_inr=50000.0,
        cadence=Cadence.LUMPSUM,
        subgroups=[],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                rank=1,
                isin="INF001",
                scheme_code="SC001",
                recommended_fund="Fund A",
            ),
        ],
    )

    db = _FakeSession()
    run_id = await svc.persist_additional_investment_recommendation(
        db,
        uuid.uuid4(),
        output,
        source_allocation_run_id=uuid.uuid4(),
        request=request,
    )

    assert isinstance(run_id, uuid.UUID)
    buys = [o for o in db.added if isinstance(o, AdditionalInvestmentBuy)]
    assert len(buys) == 1
    assert buys[0].run_id == run_id
    assert buys[0].rank == 1  # joined from request.ranked_funds by isin
    assert buys[0].scheme_code == "SC001"
    assert buys[0].monthly_amount_inr is None  # lumpsum -> no monthly framing


@pytest.mark.asyncio
async def test_request_extras_merge_over_engine_dump(monkeypatch):
    """Deficit-fill mode keys MERGE over the engine-input audit dump — both the
    engine-input keys and the mode keys must be present (spec 2026-07-03)."""
    from app.domains.additional_investment.services import (
        additional_investment_persist_service as svc,
    )

    monkeypatch.setattr(
        svc, "get_or_create_primary_portfolio", _fake_get_or_create_primary_portfolio
    )
    from app.domains.additional_investment.models import AdditionalInvestmentRun

    db = _FakeSession()
    request = _sip_request()

    await svc.persist_additional_investment_recommendation(
        db,
        uuid.uuid4(),
        _sip_output(),
        source_allocation_run_id=uuid.uuid4(),
        chat_session_id=None,
        user_question="q",
        request=request,
        request_extras={"deployment_mode": "deficit_fill", "base_corpus_inr": 750000.0},
    )

    run = next(o for o in db.added if isinstance(o, AdditionalInvestmentRun))
    # engine-input keys preserved AND mode keys merged:
    assert run.request_input["deploy_amount_inr"] == request.deploy_amount_inr
    assert run.request_input["deployment_mode"] == "deficit_fill"
    assert run.request_input["base_corpus_inr"] == 750000.0
