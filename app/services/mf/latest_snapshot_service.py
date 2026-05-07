"""Build and read user-level latest MF holdings snapshot."""

from __future__ import annotations

import math
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata, MfFundRating, MfNavHistory, MfTransaction, UserMfLatestSnapshot
from app.models.mf.enums import MfTransactionType
from app.services.mf.mf_investor_detail_service import _cagr_pct
from app.services.mf.paging import clamp_skip_limit

_OUTFLOW_TYPES = {
    MfTransactionType.BUY,
    MfTransactionType.SWITCH_IN,
    MfTransactionType.DIVIDEND_REINVEST,
}
_INFLOW_TYPES = {
    MfTransactionType.SELL,
    MfTransactionType.SWITCH_OUT,
}


def _f(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _xnpv(rate: float, cashflows: list[tuple[date, float]]) -> float:
    t0 = cashflows[0][0]
    total = 0.0
    for dt, amt in cashflows:
        years = (dt - t0).days / 365.25
        total += amt / ((1.0 + rate) ** years)
    return total


def _xirr(cashflows: list[tuple[date, float]]) -> Optional[float]:
    if len(cashflows) < 2:
        return None
    has_pos = any(v > 0 for _, v in cashflows)
    has_neg = any(v < 0 for _, v in cashflows)
    if not (has_pos and has_neg):
        return None

    lo, hi = -0.9999, 10.0
    f_lo = _xnpv(lo, cashflows)
    f_hi = _xnpv(hi, cashflows)
    if math.isnan(f_lo) or math.isnan(f_hi):
        return None
    if f_lo * f_hi > 0:
        return None

    for _ in range(80):
        mid = (lo + hi) / 2.0
        f_mid = _xnpv(mid, cashflows)
        if abs(f_mid) < 1e-7:
            return mid * 100.0
        if f_lo * f_mid <= 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return ((lo + hi) / 2.0) * 100.0


async def _latest_nav_row(db: AsyncSession, scheme_code: str) -> Optional[MfNavHistory]:
    return (
        await db.execute(
            select(MfNavHistory)
            .where(MfNavHistory.scheme_code == scheme_code)
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nav_on_or_before(db: AsyncSession, scheme_code: str, target: date) -> Optional[MfNavHistory]:
    return (
        await db.execute(
            select(MfNavHistory)
            .where(MfNavHistory.scheme_code == scheme_code, MfNavHistory.nav_date <= target)
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def rebuild_user_latest_snapshot(db: AsyncSession, user_id: uuid.UUID) -> int:
    txns = list(
        (
            await db.execute(
                select(MfTransaction)
                .where(MfTransaction.user_id == user_id)
                .order_by(MfTransaction.transaction_date.asc())
            )
        ).scalars().all()
    )

    by_scheme: dict[str, list[MfTransaction]] = {}
    for txn in txns:
        by_scheme.setdefault(txn.scheme_code, []).append(txn)

    await db.execute(delete(UserMfLatestSnapshot).where(UserMfLatestSnapshot.user_id == user_id))

    rows: list[UserMfLatestSnapshot] = []
    total_current_value = 0.0
    partial_values: dict[str, float] = {}

    for scheme_code, items in by_scheme.items():
        units = 0.0
        invested = 0.0
        cashflows: list[tuple[date, float]] = []
        for txn in items:
            t_units = _f(txn.units)
            t_amt = abs(_f(txn.amount))
            if txn.transaction_type in _OUTFLOW_TYPES:
                units += t_units
                invested += t_amt
                cashflows.append((txn.transaction_date, -t_amt))
            elif txn.transaction_type in _INFLOW_TYPES:
                units -= t_units
                invested -= t_amt
                cashflows.append((txn.transaction_date, t_amt))

        if units <= 0:
            continue

        meta = (
            await db.execute(select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code))
        ).scalar_one_or_none()
        rating = (
            await db.execute(select(MfFundRating).where(MfFundRating.scheme_code == scheme_code))
        ).scalar_one_or_none()
        nav = await _latest_nav_row(db, scheme_code)
        curr_nav = _f(nav.nav) if nav else None
        curr_value = units * curr_nav if curr_nav is not None else 0.0
        pnl = curr_value - invested
        abs_pct = (pnl / invested * 100.0) if invested > 0 else None

        if nav and curr_value > 0:
            cashflows_for_xirr = [*cashflows, (nav.nav_date, curr_value)]
            xirr_pct = _xirr(cashflows_for_xirr)
        else:
            xirr_pct = None

        one_y = three_y = five_y = None
        if nav:
            nav_1y = await _nav_on_or_before(db, scheme_code, nav.nav_date - timedelta(days=365))
            nav_3y = await _nav_on_or_before(db, scheme_code, nav.nav_date - timedelta(days=365 * 3))
            nav_5y = await _nav_on_or_before(db, scheme_code, nav.nav_date - timedelta(days=365 * 5))
            if nav_1y:
                one_y = ((_f(nav.nav) / _f(nav_1y.nav)) - 1.0) * 100.0 if _f(nav_1y.nav) > 0 else None
            if nav_3y:
                three_y = _cagr_pct(_f(nav_3y.nav), _f(nav.nav), 3.0)
            if nav_5y:
                five_y = _cagr_pct(_f(nav_5y.nav), _f(nav.nav), 5.0)

        snap = UserMfLatestSnapshot(
            user_id=user_id,
            scheme_code=scheme_code,
            isin=(meta.isin if meta else None) or (items[-1].isin if items else None),
            fund_name=(meta.scheme_name if meta else None) or (items[-1].fund_name if items else None),
            amc_name=meta.amc_name if meta else None,
            category=(meta.category if meta else None) or (items[-1].category if items else None),
            sub_category=(meta.sub_category if meta else None) or (items[-1].sub_category if items else None),
            sub_group=(rating.asset_subgroup if rating else None) or (items[-1].sub_group if items else None),
            invested_amount=round(invested, 2),
            current_units=round(units, 4),
            avg_nav=round(invested / units, 4) if units > 0 else None,
            current_nav=round(curr_nav, 4) if curr_nav is not None else None,
            current_value=round(curr_value, 2),
            unrealized_pnl=round(pnl, 2),
            absolute_return_pct=round(abs_pct, 4) if abs_pct is not None else None,
            xirr_pct=round(xirr_pct, 4) if xirr_pct is not None else None,
            portfolio_weight_pct=None,
            return_1y_pct=round(one_y, 4) if one_y is not None else None,
            return_3y_pct=round(three_y, 4) if three_y is not None else None,
            return_5y_pct=round(five_y, 4) if five_y is not None else None,
            first_investment_date=items[0].transaction_date if items else None,
            last_transaction_date=items[-1].transaction_date if items else None,
            nav_date=nav.nav_date if nav else None,
            transactions_count=len(items),
            folio_number=items[-1].folio_number if items else None,
        )
        rows.append(snap)
        partial_values[scheme_code] = curr_value
        total_current_value += curr_value

    if total_current_value > 0:
        for row in rows:
            wt = partial_values.get(row.scheme_code, 0.0) / total_current_value * 100.0
            row.portfolio_weight_pct = round(wt, 4)

    db.add_all(rows)
    await db.commit()
    return len(rows)


async def rebuild_all_users_latest_snapshot(db: AsyncSession) -> tuple[int, int]:
    """Rebuild latest snapshot rows for every user who has MF transactions.

    Returns:
        tuple[int, int]: (users_processed, total_snapshot_rows_written)
    """
    user_ids = list(
        (
            await db.execute(
                select(MfTransaction.user_id).distinct()
            )
        ).scalars().all()
    )
    users_processed = 0
    total_rows = 0
    for user_id in user_ids:
        total_rows += await rebuild_user_latest_snapshot(db, user_id)
        users_processed += 1
    return users_processed, total_rows


async def list_user_latest_snapshot(
    db: AsyncSession, user_id: uuid.UUID, *, skip: int = 0, limit: int = 100
) -> list[UserMfLatestSnapshot]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(UserMfLatestSnapshot)
        .where(UserMfLatestSnapshot.user_id == user_id)
        .order_by(UserMfLatestSnapshot.current_value.desc())
        .offset(skip)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())
