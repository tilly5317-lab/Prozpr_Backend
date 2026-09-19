"""Persist round-trip through REAL sqlite tables (JSON columns included).

Guards the request_extras JSON-serialisability contract (spec 2026-07-05 /
audit F4): a raw UUID in request_extras breaks json.dumps at flush, and the
orchestrator's best-effort except would swallow it — so this must round-trip
through a real column, not the suite's usual fake-db stand-ins.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    AdditionalInvestmentTarget,
)
from app.domains.additional_investment.services import (
    additional_investment_persist_service as persist_mod,
)

ensure_ai_agents_path()

from additional_investment.models import (  # noqa: E402
    AdditionalInvestmentInput,
    Cadence,
    RankedFund,
    SubgroupBucketAmounts,
)
from additional_investment.pipeline import run_additional_investment  # noqa: E402


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(AdditionalInvestmentRun.__table__.create)
        await conn.run_sync(AdditionalInvestmentTarget.__table__.create)
        await conn.run_sync(AdditionalInvestmentBuy.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


async def test_sip_rebal_run_persists_through_real_json_column(db_session):
    inp = AdditionalInvestmentInput(
        deploy_amount_inr=10000.0,
        cadence=Cadence.SIP_MONTHLY,
        subgroups=[
            SubgroupBucketAmounts(
                subgroup="large_cap_equities", long_term=100.0, total=100.0
            )
        ],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="large_cap_equities", sub_category="Large Cap Fund",
                rank=1, isin="INF001", scheme_code="L1",
                recommended_fund="Alpha Large Cap",
            )
        ],
        rebal_buy_isins_by_subgroup={"large_cap_equities": ["INF001"]},
        # Floor makes the cap non-binding (10% × 10k = 1k would fragment).
        sip_fund_cap_floor_inr=10000.0,
    )
    output = run_additional_investment(inp)
    rebal_run_id = uuid.uuid4()

    with patch.object(
        persist_mod,
        "get_or_create_primary_portfolio",
        new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    ):
        run_id = await persist_mod.persist_additional_investment_recommendation(
            db_session,
            uuid.uuid4(),
            output,
            source_allocation_run_id=uuid.uuid4(),
            chat_session_id=None,
            user_question="start a sip of 10k",
            request=inp,
            request_extras={"sip_rebal_run_id": str(rebal_run_id)},
        )
        await db_session.commit()  # the real flush is where a raw UUID would blow up

    run = (
        await db_session.execute(
            select(AdditionalInvestmentRun).where(
                AdditionalInvestmentRun.id == run_id
            )
        )
    ).scalar_one()
    assert run.request_input["sip_rebal_run_id"] == str(rebal_run_id)
    assert run.request_input["rebal_buy_isins_by_subgroup"] == {
        "large_cap_equities": ["INF001"]
    }
    buys = (
        (
            await db_session.execute(
                select(AdditionalInvestmentBuy).where(
                    AdditionalInvestmentBuy.run_id == run_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(b.isin, b.rank, b.scheme_code) for b in buys] == [("INF001", 1, "L1")]
    assert buys[0].monthly_amount_inr == 10000
