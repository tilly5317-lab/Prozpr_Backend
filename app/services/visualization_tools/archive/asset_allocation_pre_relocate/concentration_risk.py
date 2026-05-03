"""Chart tool — concentration risk (top-N holdings vs the rest).

Reads PortfolioHolding rows for the user's primary portfolio, sorts by
current_value desc, splits into top-N + rest, and returns a ConcentrationRisk
payload. Returns None when no portfolio or no holdings exist.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioHolding
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.schema import (
    ConcentrationHolding,
    ConcentrationRisk,
)

_TOP_N = 5
_OK_THRESHOLD = 50.0
_WATCH_THRESHOLD = 70.0


def _severity_for(top_pct: float) -> str:
    if top_pct < _OK_THRESHOLD:
        return "ok"
    if top_pct < _WATCH_THRESHOLD:
        return "watch"
    return "act"


async def build_concentration_risk(
    db: AsyncSession, user_id: uuid.UUID
) -> ConcentrationRisk | None:
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = (
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio.id)
        .order_by(PortfolioHolding.current_value.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None

    total = sum(float(r.current_value) for r in rows)
    if total <= 0:
        return None

    top_rows = rows[:_TOP_N]
    rest_rows = rows[_TOP_N:]

    top_holdings = [
        ConcentrationHolding(
            label=r.instrument_name,
            value=float(r.current_value),
            percentage=float(r.current_value) / total * 100.0,
        )
        for r in top_rows
    ]
    top_pct = sum(h.percentage for h in top_holdings)
    rest_pct = max(0.0, 100.0 - top_pct)
    rest_count = len(rest_rows)
    severity = _severity_for(top_pct)

    if rest_count == 0:
        headline = (
            f"Your portfolio holds only {len(top_holdings)} fund"
            f"{'s' if len(top_holdings) != 1 else ''} — highly concentrated"
        )
    else:
        headline = (
            f"Top {_TOP_N} funds = {top_pct:.0f}% of portfolio"
        )

    return ConcentrationRisk(
        title="Concentration Risk",
        headline=headline,
        severity=severity,
        top_n=len(top_holdings),
        top_holdings=top_holdings,
        rest_percentage=rest_pct,
        rest_count=rest_count,
    )
