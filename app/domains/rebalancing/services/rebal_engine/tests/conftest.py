"""Async DB fixtures for rebal_engine tests.

Restored 2026-07-06 from the pre-DDD ai_bridge conftest (deleted in bd773ada
when the tests moved without it) and modernised: imports point at the
domain-driven module layout, the allocation-cache fixtures write
``AssetAllocationRun`` + ``PortfolioAllocationSnapshot`` rows (the
``RebalancingRecommendation`` model they used is gone), and Postgres ``ARRAY``
columns are swapped to ``JSON`` at test time so ``Base.metadata.create_all``
works on sqlite again.

Each test gets a fully isolated per-test in-memory SQLite engine;
``app.core.database``'s ``@compiles(JSONB, "sqlite")`` shim makes JSONB
portable. Fresh engine + session per test, disposed at teardown — no
cross-test pollution and no contact with local dev DBs.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from sqlalchemy import ARRAY, JSON  # noqa: N811 -- base ARRAY also matches the postgresql dialect subclass
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect import: register every model with ``Base.metadata`` so
# ``create_all`` materialises the entire schema (FK targets like ``users`` and
# ``mf_fund_metadata`` must exist before children are created).
import app.all_models  # noqa: F401  -- registers all ORM tables with Base.metadata
from app.core.database import Base
from app.domains.mutual_funds.models.enums import (
    MfOptionType,
    MfPlanType,
    MfTransactionSource,
    MfTransactionType,
)
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.mutual_funds.models.mf_transaction import MfTransaction
from app.domains.profile.models.tax_profile import TaxProfile
from app.domains.identity.models.user import User

# Postgres ARRAY columns (rebalancing_warnings.affected_isins, cashflow
# plan_run levers) can neither be created nor bound on sqlite. Swap them to
# JSON for the test process — DDL and list-binding both work, and the swap is
# test-only (this module never loads in production).
for _table in Base.metadata.tables.values():
    for _column in _table.columns:
        if isinstance(_column.type, ARRAY):
            _column.type = JSON()


# scheme_code -> isin mapping for holdings created by THIS test. The ledger
# resolves scheme->ISIN via the MF_Logics disk CSVs (_disk_cache) where test
# scheme codes don't exist, so an autouse fixture patches the ledger's
# resolver to consult this registry first (CSV fallback for real codes).
_SCHEME_ISIN_REGISTRY: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _patch_scheme_to_isin(monkeypatch):
    from app.domains.rebalancing.services.rebal_engine import (
        holdings_ledger as _hl,
    )

    _SCHEME_ISIN_REGISTRY.clear()
    _real = _hl._scheme_to_isin

    def _resolve(scheme_codes: set[str]) -> dict[str, str]:
        out = {c: _SCHEME_ISIN_REGISTRY[c] for c in scheme_codes
               if c in _SCHEME_ISIN_REGISTRY}
        missing = set(scheme_codes) - set(out)
        if missing:
            try:
                out.update(_real(missing))
            except Exception:  # noqa: BLE001 -- CSVs absent in some envs
                pass
        return out

    monkeypatch.setattr(_hl, "_scheme_to_isin", _resolve)
    yield
    _SCHEME_ISIN_REGISTRY.clear()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test in-memory SQLite session; engine disposed at teardown."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _prime_user_graph(user: User) -> User:
    """Mark the profile relationships the allocation input-builder chain reads
    as loaded-and-empty, so attribute access never fires lazy async IO
    (production eager-loads the full graph via ``get_ai_user_context``)."""
    from sqlalchemy.orm.attributes import set_committed_value

    for rel, empty in (
        ("effective_risk_assessment", None),
        ("investment_profile", None),
        ("personal_finance_profile", None),
        ("risk_profile", None),
        ("tax_profile", None),
        ("financial_goals", []),
        ("portfolios", []),
        ("saved_investment_preference", None),
    ):
        if hasattr(type(user), rel):
            set_committed_value(user, rel, empty)
    return user


@pytest_asyncio.fixture
async def fixture_user(db_session: AsyncSession) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return _prime_user_graph(user)


@pytest_asyncio.fixture
async def fixture_buy_txn_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfTransaction]]:
    async def _make(
        *, user: User, scheme_code: str,
        units: Decimal, nav: Decimal, txn_date: date,
    ) -> MfTransaction:
        await _ensure_fund_metadata(db_session, scheme_code)
        txn = MfTransaction(
            user_id=user.id,
            scheme_code=scheme_code,
            folio_number="TEST_FOLIO",
            transaction_type=MfTransactionType.BUY,
            transaction_date=txn_date,
            units=units,
            nav=nav,
            amount=units * nav,
            source_system=MfTransactionSource.MANUAL,
        )
        db_session.add(txn)
        await db_session.flush()
        return txn
    return _make


@pytest_asyncio.fixture
async def fixture_sell_txn_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfTransaction]]:
    async def _make(
        *, user: User, scheme_code: str,
        units: Decimal, nav: Decimal, txn_date: date,
    ) -> MfTransaction:
        await _ensure_fund_metadata(db_session, scheme_code)
        txn = MfTransaction(
            user_id=user.id,
            scheme_code=scheme_code,
            folio_number="TEST_FOLIO",
            transaction_type=MfTransactionType.SELL,
            transaction_date=txn_date,
            units=units,
            nav=nav,
            amount=units * nav,
            source_system=MfTransactionSource.MANUAL,
        )
        db_session.add(txn)
        await db_session.flush()
        return txn
    return _make


@pytest_asyncio.fixture
async def fixture_nav_isin_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfNavHistory]]:
    async def _make(
        *, scheme_code: str, isin: str,
        nav: Decimal | None = None, on_date: date | None = None,
    ) -> MfNavHistory:
        await _ensure_fund_metadata(db_session, scheme_code)
        _SCHEME_ISIN_REGISTRY[scheme_code] = isin
        row = MfNavHistory(
            scheme_code=scheme_code,
            isin=isin,
            scheme_name=f"Scheme {scheme_code}",
            mf_type="EQUITY",
            nav=nav if nav is not None else Decimal("100"),
            nav_date=on_date or date.today(),
        )
        db_session.add(row)
        await db_session.flush()
        return row
    return _make


async def _ensure_fund_metadata(db_session: AsyncSession, scheme_code: str) -> None:
    """Idempotent: insert ``MfFundMetadata`` if not already present."""
    from sqlalchemy import select

    existing = (await db_session.execute(
        select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code)
    )).scalar_one_or_none()
    if existing is not None:
        return
    db_session.add(MfFundMetadata(
        scheme_code=scheme_code,
        scheme_name=f"Scheme {scheme_code}",
        amc_name="Test AMC",
        category="Equity",
        sub_category="Large Cap Fund",
        plan_type=MfPlanType.DIRECT,
        option_type=MfOptionType.GROWTH,
        is_active=True,
    ))
    await db_session.flush()


# ── Input-builder fixtures ───────────────────────────────────────────────────


# rank-1 ISIN of low_beta_equities in the canonical CSV
_RANK1_ISIN = "INF109K016L0"  # ICICI Prudential Large Cap


@pytest_asyncio.fixture
async def fixture_user_with_dob(db_session: AsyncSession) -> User:
    """User with a date_of_birth (some downstream code expects it)."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_dob_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return _prime_user_graph(user)


@pytest.fixture
def fixture_goal_allocation_output_one_subgroup():
    """Minimal allocation view with exactly one subgroup row for input-builder tests."""
    from app.domains.rebalancing.services.rebal_engine.cached_allocation import (
        CachedAssetAllocationView,
    )

    return CachedAssetAllocationView({
        "aggregated_subgroups": [
            {"subgroup": "low_beta_equities", "total": 1_000_000.0},
        ],
    })


def _serialised_one_subgroup_allocation() -> dict:
    """JSON payload mirroring ``fixture_goal_allocation_output_one_subgroup``."""
    return {
        "aggregated_subgroups": [
            {"subgroup": "low_beta_equities", "total": 1_000_000.0},
        ],
        "grand_total": 1_000_000.0,
        "all_amounts_in_multiples_of_100": True,
    }


_PRACTICAL_STUB_CACHE = None


def practical_output_stub():
    """A real ``PracticalAllocationOutput`` from a minimal profile.

    ``RebalancingComputeResponse.practical_allocation`` is required since
    Part C; running the pure practical pipeline once (cached module-level) is
    cheaper and truer than hand-building the nested output. Import from tests:
    ``from .conftest import practical_output_stub``.
    """
    global _PRACTICAL_STUB_CACHE
    if _PRACTICAL_STUB_CACHE is None:
        from app.domains.ai_engine.common import ensure_ai_agents_path

        ensure_ai_agents_path()

        from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
            PracticalAllocationInput,
            run_practical_allocation,
        )

        _PRACTICAL_STUB_CACHE = run_practical_allocation(PracticalAllocationInput(
            effective_risk_score=5.5, age=40, annual_income=2_000_000,
            osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
            shortfall_amount=0.0, total_corpus=1_000_000.0,
            monthly_household_expense=100_000, effective_tax_rate=15.0,
            net_financial_assets=1_000_000.0, goals=[],
            mf_corpus=1_000_000.0, non_mf_equity_corpus=0, elss_corpus=0,
        ))
    return _PRACTICAL_STUB_CACHE


@pytest.fixture
def fixture_one_subgroup_ranking(monkeypatch):
    """Restrict ``get_fund_ranking()`` to ``low_beta_equities`` only.

    The full CSV has many subgroups and ISINs; tests would otherwise need to
    seed NAVs for all of them. Patching the module-level reference inside
    ``input_builder`` keeps test data minimal.
    """
    from app.domains.rebalancing.services.rebal_engine import fund_rank as fr_mod
    from app.domains.rebalancing.services.rebal_engine import input_builder as ib_mod

    full = fr_mod.get_fund_ranking()
    constrained = {"low_beta_equities": full["low_beta_equities"]}
    monkeypatch.setattr(ib_mod, "get_fund_ranking", lambda: constrained)
    return constrained


@pytest_asyncio.fixture
async def fixture_seed_low_beta_navs(db_session: AsyncSession) -> None:
    """Seed a fallback MfNavHistory + MfFundMetadata row for every rank in low_beta_equities.

    Inserted at an old date so a holding fixture's today-NAV wins as
    ``_latest_nav_by_isin``. NAV=100 is a placeholder for non-held ranks the
    builder still needs to price.
    """
    from app.domains.rebalancing.services.rebal_engine import fund_rank as fr_mod

    seed_date = date(2020, 1, 1)
    rows = fr_mod.get_fund_ranking().get("low_beta_equities", [])
    for rr in rows:
        scheme_code = f"SCH_{rr.isin}"
        _SCHEME_ISIN_REGISTRY[scheme_code] = rr.isin
        await _ensure_fund_metadata(db_session, scheme_code)
        db_session.add(MfNavHistory(
            scheme_code=scheme_code,
            isin=rr.isin,
            scheme_name=rr.fund_name,
            mf_type="EQUITY",
            nav=Decimal("100"),
            nav_date=seed_date,
        ))
    await db_session.flush()


# Subgroups covering an equity beta ladder + a debt sleeve + a gold sleeve —
# wide enough for a gold or debt what-if to land on a real recommended fund
# instead of finding an empty universe.
_MULTI_SUBGROUP_NAMES = (
    "low_beta_equities",
    "medium_beta_equities",
    "high_beta_equities",
    "short_debt",
    "gold_commodities",
)


@pytest.fixture
def fixture_multi_subgroup_ranking(monkeypatch):
    """Restrict ``get_fund_ranking()`` to an equity beta ladder + short_debt +
    gold_commodities, instead of ``fixture_one_subgroup_ranking``'s single
    ``low_beta_equities``.

    Same patching approach as ``fixture_one_subgroup_ranking`` (module-level
    reference inside ``input_builder``) — only the subgroup set is wider, so
    a gold/debt preference what-if has a real rank-1 fund to buy.
    """
    from app.domains.rebalancing.services.rebal_engine import fund_rank as fr_mod
    from app.domains.rebalancing.services.rebal_engine import input_builder as ib_mod

    full = fr_mod.get_fund_ranking()
    constrained = {sg: full[sg] for sg in _MULTI_SUBGROUP_NAMES}
    monkeypatch.setattr(ib_mod, "get_fund_ranking", lambda: constrained)
    return constrained


@pytest_asyncio.fixture
async def fixture_seed_multi_subgroup_navs(db_session: AsyncSession) -> None:
    """Seed a fallback MfNavHistory + MfFundMetadata row for every rank across
    the multi-subgroup universe (low/medium/high beta, short_debt, gold).

    Same convention as ``fixture_seed_low_beta_navs``: old-dated NAV=100
    placeholders so a holding fixture's today-NAV wins as
    ``_latest_nav_by_isin``.
    """
    from app.domains.rebalancing.services.rebal_engine import fund_rank as fr_mod

    seed_date = date(2020, 1, 1)
    ranking = fr_mod.get_fund_ranking()
    for subgroup in _MULTI_SUBGROUP_NAMES:
        for rr in ranking.get(subgroup, []):
            scheme_code = f"SCH_{rr.isin}"
            _SCHEME_ISIN_REGISTRY[scheme_code] = rr.isin
            await _ensure_fund_metadata(db_session, scheme_code)
            db_session.add(MfNavHistory(
                scheme_code=scheme_code,
                isin=rr.isin,
                scheme_name=rr.fund_name,
                mf_type="EQUITY",
                nav=Decimal("100"),
                nav_date=seed_date,
            ))
    await db_session.flush()


async def _add_holding(
    db: AsyncSession,
    *,
    user: User,
    scheme_code: str,
    isin: str,
    units: Decimal,
    nav: Decimal,
    txn_date: date,
    asset_subgroup: str = "low_beta_equities",
    sub_category: str = "Large Cap Fund",
) -> None:
    """Insert MfFundMetadata + MfNavHistory + a BUY MfTransaction together."""
    from sqlalchemy import select

    _SCHEME_ISIN_REGISTRY[scheme_code] = isin

    existing = (await db.execute(
        select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code)
    )).scalar_one_or_none()
    if existing is None:
        db.add(MfFundMetadata(
            scheme_code=scheme_code,
            scheme_name=f"Scheme {scheme_code}",
            amc_name="Test AMC",
            category="Equity",
            sub_category=sub_category,
            plan_type=MfPlanType.DIRECT,
            option_type=MfOptionType.GROWTH,
            is_active=True,
        ))
    db.add(MfNavHistory(
        scheme_code=scheme_code,
        isin=isin,
        scheme_name=f"Scheme {scheme_code}",
        mf_type="EQUITY",
        nav=nav,
        nav_date=date.today(),
    ))
    db.add(MfTransaction(
        user_id=user.id,
        scheme_code=scheme_code,
        folio_number="TEST_FOLIO",
        transaction_type=MfTransactionType.BUY,
        transaction_date=txn_date,
        units=units,
        nav=nav,
        amount=units * nav,
        source_system=MfTransactionSource.MANUAL,
    ))
    await db.flush()


@pytest_asyncio.fixture
async def fixture_user_with_holdings(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> tuple[User, str]:
    """User holding 10 units of the rank-1 fund at NAV 60 (cost 50)."""
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{_RANK1_ISIN}",
        isin=_RANK1_ISIN,
        units=Decimal("10"),
        nav=Decimal("60"),
        txn_date=date(2024, 1, 1),
    )
    return fixture_user_with_dob, _RANK1_ISIN


@pytest_asyncio.fixture
async def fixture_user_with_bad_holding(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> User:
    """User holding an ISIN that is NOT in the fund-rank CSV."""
    bad_isin = "INF000BAD0001"
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code="BAD_SCHEME_001",
        isin=bad_isin,
        units=Decimal("3"),
        nav=Decimal("50"),
        txn_date=date(2024, 1, 1),
        asset_subgroup="low_beta_equities",
        sub_category="Large Cap Fund",
    )
    return fixture_user_with_dob


@pytest_asyncio.fixture
async def fixture_user_with_two_holdings(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> User:
    """User holding two distinct ISINs at known NAVs (60 and 80)."""
    rank1 = _RANK1_ISIN
    rank2 = "INF179K01YV8"  # rank-2 of low_beta_equities (HDFC Large Cap)
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{rank1}",
        isin=rank1,
        units=Decimal("10"),
        nav=Decimal("60"),
        txn_date=date(2024, 1, 1),
    )
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{rank2}",
        isin=rank2,
        units=Decimal("5"),
        nav=Decimal("80"),
        txn_date=date(2024, 1, 1),
    )
    return fixture_user_with_dob


@pytest_asyncio.fixture
async def fixture_user_with_holdings_no_tax_profile(
    db_session: AsyncSession, fixture_user_with_holdings: tuple[User, str],
) -> User:
    """Alias for fixture_user_with_holdings — no TaxProfile attached by default."""
    user, _ = fixture_user_with_holdings
    # Sanity: ensure we really have no TaxProfile row.
    from sqlalchemy import select

    existing = (await db_session.execute(
        select(TaxProfile).where(TaxProfile.user_id == user.id)
    )).scalar_one_or_none()
    assert existing is None
    return user


# ── Persistence fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def fixture_rebalancing_response():
    """Minimal, valid ``RebalancingComputeResponse`` for persistence tests."""
    from datetime import datetime

    from app.domains.ai_engine.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from Rebalancing.models import (  # type: ignore[import-not-found]
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
    )

    return RebalancingComputeResponse(
        rows=[],
        subgroups=[],
        totals=RebalancingTotals(
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            net_cash_flow_inr=Decimal(0),
            total_stcg_realised=Decimal(0),
            total_ltcg_realised=Decimal(0),
            total_stcg_net_off=Decimal(0),
            total_tax_estimate_inr=Decimal(0),
            unrebalanced_remainder_inr=Decimal(0),
            rows_count=0,
            funds_to_buy_count=0,
            funds_to_sell_count=0,
            funds_to_exit_count=0,
            funds_held_count=0,
        ),
        metadata=RebalancingRunMetadata(
            computed_at=datetime(2026, 4, 29, 12, 0, 0),
            engine_version="test-1.0.0",
            request_corpus_inr=Decimal(0),
            knob_snapshot=KnobSnapshot(
                multi_fund_cap_pct=20.0,
                others_fund_cap_pct=10.0,
                rebalance_min_change_pct=0.10,
                exit_floor_rating=5,
                ltcg_annual_exemption_inr=Decimal("125000"),
                stcg_rate_equity_pct=20.0,
                ltcg_rate_equity_pct=12.5,
                st_threshold_months_equity=12,
                st_threshold_months_debt=24,
                multi_fund_cap_subgroups=[],
            ),
            request_id=uuid.uuid4(),
        ),
        trade_list=[],
        practical_allocation=practical_output_stub(),
    )


async def _get_or_create_primary_portfolio(db: AsyncSession, user_id: uuid.UUID):
    from sqlalchemy import select

    from app.domains.portfolio.models.portfolio import Portfolio

    portfolio = (await db.execute(
        select(Portfolio).where(
            Portfolio.user_id == user_id, Portfolio.is_primary == True  # noqa: E712
        )
    )).scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(user_id=user_id, name="Primary", is_primary=True)
        db.add(portfolio)
        await db.flush()
    return portfolio


async def _insert_allocation_row(
    db: AsyncSession, user_id: uuid.UUID, *, age_days: int,
):
    """Insert an ``AssetAllocationRun`` + matching IDEAL snapshot ``age_days`` old.

    Mirrors what the aa_engine persist path writes — the rebal service's
    ``_load_cached_allocation`` reads the run header for freshness and the
    snapshot's ``allocation["goal_allocation_output"]`` for the payload.
    """
    from datetime import datetime, timedelta, timezone

    from app.domains.asset_allocation.models.run import AssetAllocationRun
    from app.domains.mutual_funds.models.enums import PortfolioSnapshotKind
    from app.domains.mutual_funds.models.mf_allocation_snapshot import (
        PortfolioAllocationSnapshot,
    )

    portfolio = await _get_or_create_primary_portfolio(db, user_id)
    created = datetime.now(timezone.utc) - timedelta(days=age_days)

    run = AssetAllocationRun(
        user_id=user_id,
        portfolio_id=portfolio.id,
        user_question="Test allocation snapshot",
        client_age=36,
        client_effective_risk_score=5.5,
        total_corpus=1_000_000.0,
        grand_total=1_000_000.0,
        created_at=created,
    )
    db.add(run)
    db.add(PortfolioAllocationSnapshot(
        user_id=user_id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation={"goal_allocation_output": _serialised_one_subgroup_allocation()},
        effective_at=created,
        source="test",
        created_at=created,
    ))
    await db.flush()
    return run


@pytest_asyncio.fixture
async def fixture_allocation_row(
    db_session: AsyncSession, fixture_user_with_dob: User,
):
    """A persisted allocation run rebalancing rows can FK back to."""
    return await _insert_allocation_row(
        db_session, fixture_user_with_dob.id, age_days=1
    )


# ── Service fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def fixture_user_no_dob(db_session: AsyncSession) -> User:
    """User without a date_of_birth set."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_no_dob_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return _prime_user_graph(user)


@pytest_asyncio.fixture
async def fixture_user_with_dob_no_holdings(db_session: AsyncSession) -> User:
    """User with date_of_birth but no MfTransaction rows."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_no_holdings_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return _prime_user_graph(user)


@pytest_asyncio.fixture
async def fixture_recent_allocation_row(
    db_session: AsyncSession, fixture_user_with_holdings: tuple[User, str],
):
    user, _ = fixture_user_with_holdings
    return await _insert_allocation_row(db_session, user.id, age_days=1)


@pytest_asyncio.fixture
async def fixture_old_allocation_row(
    db_session: AsyncSession, fixture_user_with_holdings: tuple[User, str],
):
    user, _ = fixture_user_with_holdings
    return await _insert_allocation_row(db_session, user.id, age_days=180)


@pytest.fixture
def fixture_goal_allocation_outcome(fixture_goal_allocation_output_one_subgroup):
    """An ``AllocationRunOutcome`` carrying the canonical one-subgroup output."""
    from app.domains.asset_allocation.services.aa_engine.service import (
        AllocationRunOutcome,
    )

    return AllocationRunOutcome(
        result=fixture_goal_allocation_output_one_subgroup,
        blocking_message=None,
        asset_allocation_run_id=uuid.uuid4(),
    )


@pytest_asyncio.fixture
async def fixture_user_with_elss_holding(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> tuple[User, str]:
    """User with one ELSS MF holding (asset_subgroup='tax_efficient_equities')."""
    elss_isin = "INF846K01EW2"
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{elss_isin}",
        isin=elss_isin,
        units=Decimal("100"),
        nav=Decimal("50"),
        txn_date=date(2024, 1, 1),
        asset_subgroup="tax_efficient_equities",
        sub_category="ELSS",
    )
    return fixture_user_with_dob, elss_isin


@pytest_asyncio.fixture
async def fixture_user_with_stock_holding(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> tuple[User, float]:
    """User with one direct-stock holding; returns (user, expected_total_inr)."""
    from sqlalchemy import select

    from app.domains.equities.models.company_metadata import CompanyMetadata
    from app.domains.equities.models.enums import StockTransactionType
    from app.domains.equities.models.equity_transaction import StockTransaction

    existing = (await db_session.execute(
        select(CompanyMetadata).where(CompanyMetadata.symbol == "RELIANCE")
    )).scalar_one_or_none()
    if existing is None:
        db_session.add(
            CompanyMetadata(symbol="RELIANCE", company_name="Reliance Industries")
        )
        await db_session.flush()

    buy_amount = 200_000.0  # 100 shares @ 2000
    db_session.add(StockTransaction(
        user_id=fixture_user_with_dob.id,
        symbol="RELIANCE",
        transaction_type=StockTransactionType.BUY,
        transaction_date=date(2024, 1, 1),
        quantity=Decimal("100"),
        price=Decimal("2000"),
        amount=Decimal(str(buy_amount)),
    ))
    await db_session.flush()
    return fixture_user_with_dob, buy_amount
