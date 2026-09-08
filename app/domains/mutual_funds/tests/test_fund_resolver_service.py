"""Tests for the fund resolver — fund identity (_match_fund_family) and variant
canonicalization to Direct-Growth (_pick_direct_growth), tested separately (P6)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401
from app.domains.mutual_funds.models.enums import MfOptionType, MfPlanType
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.services.fund_resolver_service import (
    Ambiguous,
    ResolvedFund,
    _match_fund_family,
    _pick_direct_growth,
    resolve_fund,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(MfFundMetadata.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _meta(code, name, isin, plan, option, sub="Flexi Cap Fund"):
    return MfFundMetadata(
        id=uuid.uuid4(), scheme_code=code, isin=isin, scheme_name=name,
        amc_name="AMC", category="Equity", sub_category=sub,
        plan_type=plan, option_type=option, is_active=True,
    )


def test_pick_direct_growth_selects_direct_growth_variant():
    # Same fund, two variants — must pick Direct-Growth.
    candidates = [
        _meta("119771", "Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "INF001",
              MfPlanType.DIRECT, MfOptionType.GROWTH),
        _meta("118989", "Parag Parikh Flexi Cap Fund - Regular Plan - Growth", "INF002",
              MfPlanType.REGULAR, MfOptionType.GROWTH),
    ]
    result = _pick_direct_growth(candidates)
    assert isinstance(result, ResolvedFund)
    assert result.scheme_code == "119771"


@pytest.mark.asyncio
async def test_match_fund_family_distinguishes_lookalikes(db_session):
    db_session.add_all([
        _meta("1", "Nippon India Growth Fund - Direct Plan - Growth", "INF010",
              MfPlanType.DIRECT, MfOptionType.GROWTH),
        _meta("2", "Nippon India Large Cap Fund - Direct Plan - Growth", "INF011",
              MfPlanType.DIRECT, MfOptionType.GROWTH),
    ])
    await db_session.commit()

    matches = await _match_fund_family(db_session, "Nippon India Large Cap")

    assert {m.scheme_code for m in matches} == {"2"}


@pytest.mark.asyncio
async def test_resolve_fund_held_uses_held_scheme(db_session):
    db_session.add(_meta("HELD1", "Kotak Technology Fund - Regular Plan - Growth", "INF020",
                         MfPlanType.REGULAR, MfOptionType.GROWTH, sub="Sectoral Fund"))
    await db_session.commit()

    result = await resolve_fund(db_session, "Kotak Technology",
                                held={"kotak technology fund": "HELD1"})

    assert isinstance(result, ResolvedFund)
    assert result.is_held is True and result.scheme_code == "HELD1"


@pytest.mark.asyncio
async def test_resolve_fund_ambiguous_two_families(db_session):
    db_session.add_all([
        _meta("A", "HDFC Flexi Cap Fund - Direct Plan - Growth", "INF030",
              MfPlanType.DIRECT, MfOptionType.GROWTH),
        _meta("B", "HDFC Balanced Advantage Fund - Direct Plan - Growth", "INF031",
              MfPlanType.DIRECT, MfOptionType.GROWTH),
    ])
    await db_session.commit()

    result = await resolve_fund(db_session, "HDFC")

    assert isinstance(result, Ambiguous)
    assert len(result.candidates) >= 2


@pytest.mark.asyncio
async def test_resolve_fund_unknown_none(db_session):
    result = await resolve_fund(db_session, "Zzz Nonexistent Fund")
    assert result is None
