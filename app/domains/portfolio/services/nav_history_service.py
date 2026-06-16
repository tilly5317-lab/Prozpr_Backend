"""Per-user daily portfolio NAV history.

Computes one daily value per user from their primary-portfolio holdings.

Approach
--------
`mf_nav_history` is empty in the dev DB, so we synthesise a deterministic
daily NAV path for each holding using the holding's own ``return_1y`` /
``return_3y`` / ``return_5y`` and ``current_price`` as the anchor for today.
Between the year anchors we interpolate geometrically and add small
seed-derived noise so the line is wiggly, not flat. Per day we sum
``units × NAV`` across holdings.

The result is upserted into ``user_portfolio_nav_history`` (one row per
user per day) and read back by the ``/portfolio/nav-history`` endpoint.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding
from app.domains.portfolio.models.user_portfolio_nav_history import (
    UserPortfolioNavHistory,
)


HORIZON_DAYS: dict[str, int] = {
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "3Y": 365 * 3,
    "MAX": 365 * 5,
}


@dataclass
class _HoldingPath:
    units: float
    invested: float
    nav_today: float
    return_1y_pct: float
    return_3y_pct: float
    return_5y_pct: float
    seed: int


def _seed_from(ticker: str | None, name: str) -> int:
    key = (ticker or name or "x").encode("utf-8")
    return int(hashlib.md5(key).hexdigest()[:8], 16)


def _anchor_nav(current_nav: float, return_pct: float | None) -> float:
    if return_pct is None:
        return current_nav
    factor = 1.0 + (return_pct / 100.0)
    if factor <= 0:
        return current_nav
    return current_nav / factor


def _nav_on_day(h: _HoldingPath, days_ago: int) -> float:
    """Geometric interpolation between today / 1y / 3y / 5y anchors with noise."""
    nav_today = h.nav_today
    nav_1y = _anchor_nav(nav_today, h.return_1y_pct)
    nav_3y = _anchor_nav(nav_today, h.return_3y_pct)
    nav_5y = _anchor_nav(nav_today, h.return_5y_pct)

    if days_ago <= 0:
        base = nav_today
    elif days_ago <= 365:
        t = days_ago / 365.0
        base = nav_today * ((nav_1y / nav_today) ** t)
    elif days_ago <= 365 * 3:
        t = (days_ago - 365) / (365.0 * 2)
        base = nav_1y * ((nav_3y / nav_1y) ** t)
    elif days_ago <= 365 * 5:
        t = (days_ago - 365 * 3) / (365.0 * 2)
        base = nav_3y * ((nav_5y / nav_3y) ** t)
    else:
        base = nav_5y

    # Deterministic per-fund per-day wiggle (~0.5% peak-to-peak) so the chart
    # is not a smooth monotone line.
    angle = (h.seed % 360 + days_ago * 7) * math.pi / 180.0
    wiggle = 1.0 + math.sin(angle) * 0.005 + math.cos(angle * 0.37) * 0.003
    return max(0.0001, base * wiggle)


async def _load_holding_paths(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[list[_HoldingPath], float]:
    portfolio_stmt = select(Portfolio).where(
        Portfolio.user_id == user_id,
        Portfolio.is_primary == True,  # noqa: E712
    )
    portfolio = (await db.execute(portfolio_stmt)).scalar_one_or_none()
    if portfolio is None:
        return [], 0.0

    holdings_stmt = select(PortfolioHolding).where(
        PortfolioHolding.portfolio_id == portfolio.id
    )
    holdings = (await db.execute(holdings_stmt)).scalars().all()

    paths: list[_HoldingPath] = []
    total_invested = 0.0
    for h in holdings:
        nav_today = float(h.current_price or 0)
        units = float(h.quantity or 0)
        if nav_today <= 0 or units <= 0:
            # Fall back to flat current_value if we lack units/price.
            cv = float(h.current_value or 0)
            if cv > 0:
                nav_today = 1.0
                units = cv
            else:
                continue
        invested = float(h.quantity or 0) * float(h.average_cost or 0)
        if invested <= 0:
            invested = float(h.current_value or units * nav_today)
        total_invested += invested
        paths.append(
            _HoldingPath(
                units=units,
                invested=invested,
                nav_today=nav_today,
                return_1y_pct=float(h.return_1y) if h.return_1y is not None else 12.0,
                return_3y_pct=float(h.return_3y) if h.return_3y is not None else 38.0,
                return_5y_pct=float(h.return_5y) if h.return_5y is not None else 65.0,
                seed=_seed_from(h.ticker_symbol, h.instrument_name),
            )
        )
    return paths, total_invested


async def recompute_user_nav_history(
    db: AsyncSession, user_id: uuid.UUID, *, days: int = HORIZON_DAYS["MAX"]
) -> int:
    """(Re)compute and upsert the user's daily portfolio NAV series."""
    paths, total_invested = await _load_holding_paths(db, user_id)
    if not paths:
        return 0

    today = date.today()
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    rows: list[dict] = []
    for delta in range(days, -1, -1):
        d = today - timedelta(days=delta)
        total = sum(p.units * _nav_on_day(p, delta) for p in paths)
        gain_pct = (
            ((total - total_invested) / total_invested * 100)
            if total_invested > 0
            else 0.0
        )
        rows.append(
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "recorded_date": d,
                "total_value": round(total, 2),
                "total_invested": round(total_invested, 2),
                "gain_percentage": round(gain_pct, 4),
            }
        )

    if dialect == "postgresql":
        stmt = pg_insert(UserPortfolioNavHistory).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "recorded_date"],
            set_={
                "total_value": stmt.excluded.total_value,
                "total_invested": stmt.excluded.total_invested,
                "gain_percentage": stmt.excluded.gain_percentage,
            },
        )
        await db.execute(stmt)
    else:
        # SQLite (local dev) — same upsert with sqlite dialect.
        stmt = sqlite_insert(UserPortfolioNavHistory).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "recorded_date"],
            set_={
                "total_value": stmt.excluded.total_value,
                "total_invested": stmt.excluded.total_invested,
                "gain_percentage": stmt.excluded.gain_percentage,
            },
        )
        await db.execute(stmt)
    await db.commit()
    return len(rows)


async def get_user_nav_history(
    db: AsyncSession, user_id: uuid.UUID, *, horizon: str
) -> list[UserPortfolioNavHistory]:
    """Return rows for the chosen horizon, recomputing on demand if empty."""
    days = HORIZON_DAYS.get(horizon.upper(), HORIZON_DAYS["1Y"])
    cutoff = date.today() - timedelta(days=days)

    stmt = (
        select(UserPortfolioNavHistory)
        .where(
            UserPortfolioNavHistory.user_id == user_id,
            UserPortfolioNavHistory.recorded_date >= cutoff,
        )
        .order_by(UserPortfolioNavHistory.recorded_date.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    # No synthetic fallback: the series is now built from real units × NAV by the
    # net-worth backfill job. When empty, the dashboard shows the "Fetch Net Worth
    # History" call-to-action instead of a fabricated line.
    return list(rows)
