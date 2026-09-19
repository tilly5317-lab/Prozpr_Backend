"""Tests for screen_top_funds — DB-NAV → ranked 'best performing funds' screen.

DB-only, whole-universe, Regular-Growth plans only, ranked by trailing CAGR.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers ORM tables so FK targets resolve
from app.domains.mutual_funds.models.enums import MfOptionType, MfPlanType
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.mutual_funds.services.fund_screener_service import screen_top_funds


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(MfFundMetadata.__table__.create)
        await conn.run_sync(MfNavHistory.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _meta(
    code: str,
    *,
    name: str = "Test Fund",
    amc: str = "Test AMC",
    category: str = "Equity",
    sub_category: str | None = "Large Cap",
    plan: MfPlanType = MfPlanType.REGULAR,
    option: MfOptionType = MfOptionType.GROWTH,
    is_active: bool = True,
) -> MfFundMetadata:
    return MfFundMetadata(
        id=uuid.uuid4(), scheme_code=code, scheme_name=name, amc_name=amc,
        category=category, sub_category=sub_category, plan_type=plan,
        option_type=option, is_active=is_active,
    )


def _nav(code: str, d: dt.date, nav: float) -> MfNavHistory:
    return MfNavHistory(
        id=uuid.uuid4(), scheme_code=code, scheme_name="Test Fund",
        mf_type="EQUITY", nav=nav, nav_date=d,
    )


_AS_OF = dt.date(2026, 7, 1)
_3Y_AGO = _AS_OF - dt.timedelta(days=365 * 3)


@pytest.mark.asyncio
async def test_ranks_regular_growth_funds_by_3y_cagr_desc(db_session):
    # Two funds; A doubled over 3y (~26% CAGR), B up 50% (~14.5% CAGR).
    db_session.add_all([
        _meta("A", name="Alpha Fund"),
        _meta("B", name="Beta Fund"),
        _nav("A", _3Y_AGO, 100.0), _nav("A", _AS_OF, 200.0),
        _nav("B", _3Y_AGO, 100.0), _nav("B", _AS_OF, 150.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3, limit=5)

    assert [f.scheme_code for f in out] == ["A", "B"]
    assert out[0].scheme_name == "Alpha Fund"
    assert out[0].return_cagr_pct == pytest.approx(25.99, abs=0.05)
    assert out[1].return_cagr_pct == pytest.approx(14.47, abs=0.05)


@pytest.mark.asyncio
async def test_excludes_direct_and_idcw_plans(db_session):
    # Only the Regular-Growth fund should be ranked; Direct + IDCW variants drop.
    db_session.add_all([
        _meta("REG", name="Regular Growth", plan=MfPlanType.REGULAR, option=MfOptionType.GROWTH),
        _meta("DIR", name="Direct Growth", plan=MfPlanType.DIRECT, option=MfOptionType.GROWTH),
        _meta("IDW", name="Regular IDCW", plan=MfPlanType.REGULAR, option=MfOptionType.IDCW),
        _nav("REG", _3Y_AGO, 100.0), _nav("REG", _AS_OF, 200.0),
        _nav("DIR", _3Y_AGO, 100.0), _nav("DIR", _AS_OF, 210.0),
        _nav("IDW", _3Y_AGO, 100.0), _nav("IDW", _AS_OF, 190.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3)

    assert [f.scheme_code for f in out] == ["REG"]


@pytest.mark.asyncio
async def test_excludes_inactive_funds(db_session):
    db_session.add_all([
        _meta("ON", name="Active", is_active=True),
        _meta("OFF", name="Retired", is_active=False),
        _nav("ON", _3Y_AGO, 100.0), _nav("ON", _AS_OF, 150.0),
        _nav("OFF", _3Y_AGO, 100.0), _nav("OFF", _AS_OF, 300.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3)

    assert [f.scheme_code for f in out] == ["ON"]


@pytest.mark.asyncio
async def test_excludes_funds_without_full_horizon_history(db_session):
    # SHORT has no NAV at/before the 3y cutoff → cannot compute 3y CAGR → dropped.
    db_session.add_all([
        _meta("LONG", name="Long History"),
        _meta("SHORT", name="Short History"),
        _nav("LONG", _3Y_AGO, 100.0), _nav("LONG", _AS_OF, 150.0),
        _nav("SHORT", _AS_OF - dt.timedelta(days=200), 100.0), _nav("SHORT", _AS_OF, 180.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3)

    assert [f.scheme_code for f in out] == ["LONG"]


@pytest.mark.asyncio
async def test_excludes_funds_with_stale_end_nav(db_session):
    # STALE stopped reporting 200 days ago; ranking it on that old NAV over a
    # window that "ends" today would be misleading, so it drops out.
    db_session.add_all([
        _meta("FRESH", name="Fresh"),
        _meta("STALE", name="Stale"),
        _nav("FRESH", _3Y_AGO, 100.0), _nav("FRESH", _AS_OF, 150.0),
        _nav("STALE", _3Y_AGO, 100.0),
        _nav("STALE", _AS_OF - dt.timedelta(days=200), 400.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3)

    assert [f.scheme_code for f in out] == ["FRESH"]


@pytest.mark.asyncio
async def test_excludes_direct_by_name_when_plan_type_misclassified(db_session):
    # Real-data hazard: some schemes are named "Direct" but carry plan_type=REGULAR
    # in the source feed, so the column filter alone lets a Direct plan (and a
    # dup of the same underlying fund) leak in. Name is the backstop.
    db_session.add_all([
        _meta("REG", name="Mirae FANG FoF Regular Growth", plan=MfPlanType.REGULAR),
        _meta("DIRTRAP", name="Mirae FANG FoF Direct Growth", plan=MfPlanType.REGULAR),
        _nav("REG", _3Y_AGO, 100.0), _nav("REG", _AS_OF, 200.0),
        _nav("DIRTRAP", _3Y_AGO, 100.0), _nav("DIRTRAP", _AS_OF, 205.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3)

    assert [f.scheme_code for f in out] == ["REG"]


@pytest.mark.asyncio
async def test_respects_limit(db_session):
    metas, navs = [], []
    for i, mult in enumerate([1.1, 1.2, 1.3, 1.4, 1.5, 1.6]):
        code = f"F{i}"
        metas.append(_meta(code, name=f"Fund {i}"))
        navs.append(_nav(code, _3Y_AGO, 100.0))
        navs.append(_nav(code, _AS_OF, 100.0 * mult))
    db_session.add_all(metas + navs)
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3, limit=3)

    assert len(out) == 3
    assert [f.scheme_code for f in out] == ["F5", "F4", "F3"]  # highest CAGR first


@pytest.mark.asyncio
async def test_category_filter_restricts_universe(db_session):
    db_session.add_all([
        _meta("LC", name="Large Cap Fund", sub_category="Large Cap"),
        _meta("SC", name="Small Cap Fund", sub_category="Small Cap"),
        _nav("LC", _3Y_AGO, 100.0), _nav("LC", _AS_OF, 150.0),
        _nav("SC", _3Y_AGO, 100.0), _nav("SC", _AS_OF, 300.0),
    ])
    await db_session.commit()

    out = await screen_top_funds(db_session, horizon_years=3, category="Large Cap")

    assert [f.scheme_code for f in out] == ["LC"]


@pytest.mark.asyncio
async def test_empty_universe_returns_empty(db_session):
    out = await screen_top_funds(db_session, horizon_years=3)
    assert out == []
