"""TWR for the Returns tab (mutual-funds only) — DB adapter.

Feeds the pure ``financial_primitives.twr_wealth_index`` kernel from the user's
real data: daily portfolio values (``UserPortfolioNavHistory``), external
cashflows (``MfTransaction`` ledger), and the Nifty 50 EOD series (from the
``benchmarks`` domain via ``benchmark_data_service.load_value_series``). Mirrors
how ``benchmark_service`` wraps ``financial_primitives.xirr``. The frontend
rebases the returned full series per selected range.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_primitives import twr_wealth_index

from app.domains.benchmarks.services.benchmark_data_service import load_value_series
from app.domains.mutual_funds.models import MfTransaction
from app.domains.mutual_funds.services.txn_value import trade_value
from app.domains.mutual_funds.services.xirr_service import compute_portfolio_xirr
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
    """Assemble the user's daily TWR series (portfolio + normalized Nifty 50 TRI).

    Also carries the since-inception portfolio XIRR (money-weighted return) so the
    Performance tab's headline figures all come from this one call.
    """
    xirr_result = await compute_portfolio_xirr(db, user_id)
    portfolio_xirr = xirr_result.xirr
    as_of_date = xirr_result.as_of_date

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
        return TwrSeriesResponse(
            has_data=False, points=[], portfolio_xirr=portfolio_xirr, as_of_date=as_of_date
        )

    txn_rows = (
        await db.execute(
            select(
                MfTransaction.transaction_date,
                MfTransaction.transaction_type,
                MfTransaction.amount,
                MfTransaction.units,
                MfTransaction.nav,
            ).where(MfTransaction.user_id == user_id)
        )
    ).all()
    cashflows: dict[date, float] = defaultdict(float)
    for txn_date, txn_type, amount, units, nav in txn_rows:
        type_value = txn_type.value if hasattr(txn_type, "value") else str(txn_type)
        # A mis-parsed amount column is repriced off units x NAV (``txn_value``) —
        # an under-stated external flow distorts the whole TWR index.
        magnitude = trade_value(units, nav, amount)
        if type_value in EXTERNAL_IN_TYPES:
            cashflows[txn_date] += magnitude
        elif type_value in EXTERNAL_OUT_TYPES:
            cashflows[txn_date] -= magnitude

    tri_rows = await load_value_series(db, NIFTY_INDEX_NAME)
    tri_lookup = build_step_lookup([(d, float(v)) for d, v in tri_rows])
    # Anchor the Nifty index at the first portfolio date that has benchmark data.
    # A user whose history starts BEFORE the stored benchmark series (step lookup
    # returns None there) would otherwise lose the entire Nifty line; instead the
    # earlier points stay None and the line starts where the data does — the
    # frontend rebases each range from its first non-null benchmark point.
    baseline = tri_lookup(daily_values[0][0])
    if not baseline:
        baseline = next(
            (tri_lookup(d) for d, _ in daily_values if tri_lookup(d)), None
        )

    wealth = twr_wealth_index(daily_values, dict(cashflows))
    points: list[TwrPoint] = []
    for d, w in wealth:
        tri = tri_lookup(d)
        nifty_index = (tri / baseline) if (tri is not None and baseline) else None
        points.append(TwrPoint(date=d, portfolio_index=w, nifty_index=nifty_index))

    return TwrSeriesResponse(
        has_data=len(points) >= 2,
        points=points,
        portfolio_xirr=portfolio_xirr,
        as_of_date=as_of_date,
    )
