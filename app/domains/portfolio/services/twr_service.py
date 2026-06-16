"""TWR for the Returns tab (mutual-funds only) — DB adapter.

Feeds the pure ``financial_primitives.twr_wealth_index`` kernel from the user's
real data: daily portfolio values (``UserPortfolioNavHistory``), external
cashflows (``MfTransaction`` ledger), and the Nifty 50 TRI (``IndexTriHistory``).
Mirrors how ``benchmark_service`` wraps ``financial_primitives.xirr``. The
frontend rebases the returned full series per selected range.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_primitives import twr_wealth_index

from app.domains.mutual_funds.models import IndexTriHistory, MfTransaction
from app.domains.portfolio.models.user_portfolio_nav_history import (
    UserPortfolioNavHistory,
)
from app.domains.portfolio.schemas.portfolio import TwrPoint, TwrSeriesResponse
from app.domains.portfolio.services.benchmark_service import (
    EXTERNAL_IN_TYPES,
    EXTERNAL_OUT_TYPES,
    NIFTY_INDEX_NAME,
    build_step_lookup,
)


async def compute_twr_series(db: AsyncSession, user_id: uuid.UUID) -> TwrSeriesResponse:
    """Assemble the user's daily TWR series (portfolio + normalized Nifty 50 TRI)."""
    nav_rows = (
        await db.execute(
            select(
                UserPortfolioNavHistory.recorded_date,
                UserPortfolioNavHistory.total_value,
            )
            .where(UserPortfolioNavHistory.user_id == user_id)
            .order_by(UserPortfolioNavHistory.recorded_date.asc())
        )
    ).all()
    daily_values = [(d, float(v)) for d, v in nav_rows]
    if len(daily_values) < 2:
        return TwrSeriesResponse(has_data=False, points=[])

    txn_rows = (
        await db.execute(
            select(
                MfTransaction.transaction_date,
                MfTransaction.transaction_type,
                MfTransaction.amount,
            ).where(MfTransaction.user_id == user_id)
        )
    ).all()
    cashflows: dict[date, float] = defaultdict(float)
    for txn_date, txn_type, amount in txn_rows:
        type_value = txn_type.value if hasattr(txn_type, "value") else str(txn_type)
        magnitude = abs(float(amount))
        if type_value in EXTERNAL_IN_TYPES:
            cashflows[txn_date] += magnitude
        elif type_value in EXTERNAL_OUT_TYPES:
            cashflows[txn_date] -= magnitude

    tri_rows = (
        await db.execute(
            select(IndexTriHistory.tri_date, IndexTriHistory.tri_value).where(
                IndexTriHistory.index_name == NIFTY_INDEX_NAME
            )
        )
    ).all()
    tri_lookup = build_step_lookup([(d, float(v)) for d, v in tri_rows])
    baseline = tri_lookup(daily_values[0][0])

    wealth = twr_wealth_index(daily_values, dict(cashflows))
    points: list[TwrPoint] = []
    for d, w in wealth:
        tri = tri_lookup(d)
        nifty_index = (tri / baseline) if (tri is not None and baseline) else None
        points.append(TwrPoint(date=d, portfolio_index=w, nifty_index=nifty_index))

    return TwrSeriesResponse(has_data=len(points) >= 2, points=points)
