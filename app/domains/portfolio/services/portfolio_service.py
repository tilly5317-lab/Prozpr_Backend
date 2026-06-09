"""Application service — `portfolio_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.mutual_funds.models import MfNavHistory
from app.domains.portfolio.models.portfolio import Portfolio

logger = logging.getLogger(__name__)


async def get_primary_portfolio(db: AsyncSession, user_id: uuid.UUID) -> Portfolio | None:
    stmt = select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.is_primary == True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_or_create_primary_portfolio(db: AsyncSession, user_id: uuid.UUID) -> Portfolio:
    portfolio = await get_primary_portfolio(db, user_id)
    if not portfolio:
        portfolio = Portfolio(user_id=user_id, name="Primary", is_primary=True)
        db.add(portfolio)
        await db.flush()
    return portfolio


async def _latest_nav_on_or_before(
    db: AsyncSession, key: str, on_day: date
) -> float | None:
    """Most recent stored NAV at/behind ``on_day`` for a holding (no external fetch).

    ``key`` is a holding's ``ticker_symbol``, which may be an AMFI scheme code *or*
    an ISIN (the CAS ingest stores whichever it could resolve). ``mf_nav_history`` is
    keyed by AMFI scheme code but also carries the ISIN, so we match on either.
    """
    nav = (
        await db.execute(
            select(MfNavHistory.nav)
            .where(
                or_(
                    MfNavHistory.scheme_code == key,
                    func.upper(MfNavHistory.isin) == key.upper(),
                ),
                MfNavHistory.nav_date <= on_day,
            )
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar()
    return float(nav) if nav is not None else None


async def revalue_primary_portfolio_at_latest_nav(
    db: AsyncSession, user_id: uuid.UUID
) -> Portfolio | None:
    """Re-price the primary portfolio at *today's* NAV and refresh its aggregates.

    The CAMS ingest stores each holding's value at the statement date (a frozen
    "CAMS" valuation). This re-marks every mutual-fund holding to the latest NAV
    stored in ``mf_nav_history`` (carried forward from the most recent trading day,
    no network call) and recomputes ``total_value`` / ``total_invested`` /
    ``total_gain_percentage`` so the dashboard headline shows **today's** net worth
    — consistent with the NAV-history chart — instead of the statement-date figure.

    Idempotent and best-effort: holdings whose NAV is unknown keep their existing
    value. Returns the (mutated, committed) portfolio, or ``None`` if the user has
    no primary portfolio.
    """
    portfolio = (
        await db.execute(
            select(Portfolio)
            .options(
                selectinload(Portfolio.holdings),
                selectinload(Portfolio.allocations),
            )
            .where(Portfolio.user_id == user_id, Portfolio.is_primary == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if portfolio is None:
        return None

    holdings = list(portfolio.holdings)
    if not holdings:
        return portfolio

    today = date.today()

    # Resolve each held scheme's *latest* NAV. ``get_latest_nav_with_source_fallback``
    # reads local ``mf_nav_history`` and only reaches out to mfapi.in when the stored
    # NAV is missing or stale (>1 day) — so a freshly-ingested statement (whose NAV
    # history hasn't been backfilled yet) still gets today's price, and repeat loads
    # are served from the DB. Persisting also seeds the table for the chart series.
    from app.domains.mutual_funds.services.nav_history_service import (
        get_latest_nav_with_source_fallback,
    )

    scheme_codes = {
        h.ticker_symbol
        for h in holdings
        if h.instrument_type == "mutual_fund" and h.ticker_symbol
    }
    nav_by_scheme: dict[str, float] = {}
    for code in scheme_codes:
        try:
            row = await get_latest_nav_with_source_fallback(db, code)
        except Exception:  # noqa: BLE001 — never fail the read on a NAV lookup
            row = None
        if row is not None and row.nav is not None and float(row.nav) > 0:
            nav_by_scheme[code] = float(row.nav)

    repriced = False
    for h in holdings:
        units = float(h.quantity) if h.quantity is not None else 0.0
        if h.instrument_type == "mutual_fund" and h.ticker_symbol and units > 0:
            nav = nav_by_scheme.get(h.ticker_symbol)
            if nav is None:  # ticker may be an ISIN — try the local scheme/ISIN match
                nav = await _latest_nav_on_or_before(db, h.ticker_symbol, today)
            if nav is not None and nav > 0:
                h.current_price = round(nav, 6)
                h.current_value = round(units * nav, 2)
                repriced = True

    # Recompute the portfolio rollup from the (now re-marked) holdings.
    total_value = round(sum(float(h.current_value or 0) for h in holdings), 2)

    invested = 0.0
    for h in holdings:
        units = float(h.quantity) if h.quantity is not None else 0.0
        cost = float(h.average_cost) if h.average_cost is not None else 0.0
        if units > 0 and cost > 0:
            invested += units * cost
    # Keep the statement cost basis when holdings carry no per-unit cost.
    total_invested = round(invested, 2) if invested > 0 else float(portfolio.total_invested or 0)

    # Authoritative headline = today's net worth from the transaction *ledger* valued
    # at the latest NAV (the same source the dashboard chart uses, with scheme codes
    # that reliably match mf_nav_history). The per-holding reprice above can miss funds
    # whose ticker doesn't resolve to a NAV row; the ledger total doesn't. Use it
    # whenever it is available (>0) and fall back to the holdings sum otherwise.
    from app.domains.portfolio.services.networth_history_service import compute_today_networth

    ledger = await compute_today_networth(db, user_id)
    if ledger is not None and ledger[0] > 0:
        total_value, ledger_invested, _ = ledger
        if ledger_invested > 0:
            total_invested = ledger_invested

    for h in holdings:
        h.allocation_percentage = (
            round(100.0 * float(h.current_value or 0) / total_value, 2) if total_value > 0 else None
        )

    # Re-scale the bucket allocation amounts so the donut rupee figures track today's
    # value (the asset-class *mix* is unchanged, so percentages stay put).
    allocations = list(portfolio.allocations)
    old_alloc_total = sum(float(a.amount or 0) for a in allocations)
    if allocations and old_alloc_total > 0 and total_value > 0:
        scale = total_value / old_alloc_total
        for a in allocations:
            a.amount = round(float(a.amount or 0) * scale, 2)

    portfolio.total_value = total_value
    portfolio.total_invested = total_invested
    portfolio.total_gain_percentage = (
        round((total_value - total_invested) / total_invested * 100, 2)
        if total_invested > 0
        else None
    )

    if repriced:
        logger.debug(
            "revalued portfolio %s at latest NAV: total_value=%s invested=%s",
            portfolio.id, total_value, total_invested,
        )
    await db.commit()
    return portfolio
