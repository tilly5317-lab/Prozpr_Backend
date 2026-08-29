"""Build and read user-level latest MF holdings snapshot."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import effective_scope, scope_filter
from app.domains.mutual_funds.models import (
    MfFundMetadata,
    MfNavHistory,
    MfTransaction,
    UserMfLatestSnapshot,
)
from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.mutual_funds.services.investor_detail_service import _cagr_pct
from app.domains.mutual_funds.services.paging import clamp_skip_limit
from app.domains.mutual_funds.services.scheme_classification import classify_holding
from app.domains.mutual_funds.services.txn_value import trade_value

_OUTFLOW_TYPES = {
    MfTransactionType.BUY,
    MfTransactionType.SWITCH_IN,
    MfTransactionType.DIVIDEND_REINVEST,
}
_INFLOW_TYPES = {
    MfTransactionType.SELL,
    MfTransactionType.SWITCH_OUT,
}
_MAX_NAV_LOOKBACK_DAYS = 14

logger = logging.getLogger(__name__)

# Below this, a position is closed. Real holdings are never a millionth of a unit;
# anything smaller is floating-point residue from a fund that was fully sold.
_CLOSED_POSITION_UNITS = 1e-6

# ``avg_nav`` and ``current_nav`` are NUMERIC(12,4): |value| must stay under 10^8
# or the INSERT aborts, taking the user's whole rebuild with it. Real NAVs are two
# to five digits, so anything at this scale is corrupt input rather than a
# holding — drop the one field and keep the rest of the snapshot.
_MAX_NAV = 1e8

# The percentage columns are NUMERIC(10,4): |value| must stay under 10^6. A real
# return never reaches 1,000,000%; the mis-parsed amounts that used to produce one
# are now priced off units x NAV by ``txn_value.trade_value``, so this is the
# backstop for a corruption nobody has seen yet, not the everyday path.
_MAX_PCT = 1e6


def _f(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _fit(
    value: Optional[float], limit: float, scheme_code: str, column: str
) -> Optional[float]:
    """Round to 4dp for a NUMERIC column, or None when it would overflow it.

    One corrupt fund must not abort a user's whole rebuild — the rollback also
    reverts the DELETE, so the user keeps a frozen snapshot until someone
    notices. Dropping the single field that cannot be stored keeps every other
    holding, and the warning names the scheme to chase upstream.
    """
    if value is None:
        return None
    if not math.isfinite(value) or abs(value) >= limit:
        logger.warning(
            "scheme %s: value %r does not fit %s — storing NULL",
            scheme_code,
            value,
            column,
        )
        return None
    return round(value, 4)


def _nav_or_none(value: Optional[float], scheme_code: str) -> Optional[float]:
    """Round a NAV for a NUMERIC(12,4) column, or None if it cannot fit."""
    return _fit(value, _MAX_NAV, scheme_code, "NUMERIC(12,4)")


def _pct_or_none(value: Optional[float], scheme_code: str) -> Optional[float]:
    """Round a percentage for a NUMERIC(10,4) column, or None if it cannot fit."""
    return _fit(value, _MAX_PCT, scheme_code, "NUMERIC(10,4)")


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


async def _nav_on_or_before(
    db: AsyncSession, scheme_code: str, target: date
) -> Optional[MfNavHistory]:
    return (
        await db.execute(
            select(MfNavHistory)
            .where(
                MfNavHistory.scheme_code == scheme_code, MfNavHistory.nav_date <= target
            )
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nav_on_date(
    db: AsyncSession, scheme_code: str, target: date
) -> Optional[MfNavHistory]:
    return (
        await db.execute(
            select(MfNavHistory)
            .where(
                MfNavHistory.scheme_code == scheme_code, MfNavHistory.nav_date == target
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nav_by_walking_back(
    db: AsyncSession,
    scheme_code: str,
    anchor: date,
    *,
    max_lookback_days: int = _MAX_NAV_LOOKBACK_DAYS,
) -> Optional[MfNavHistory]:
    """Find nearest active NAV at/behind anchor by walking day-wise backwards."""
    for days_back in range(max_lookback_days + 1):
        row = await _nav_on_date(db, scheme_code, anchor - timedelta(days=days_back))
        if row:
            return row
    return None


async def rebuild_user_latest_snapshot(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    _commit: bool = True,
) -> int:
    txns = list(
        (
            await db.execute(
                select(MfTransaction)
                .where(MfTransaction.user_id == user_id)
                .order_by(MfTransaction.transaction_date.asc())
            )
        )
        .scalars()
        .all()
    )

    by_scheme: dict[str, list[MfTransaction]] = {}
    for txn in txns:
        by_scheme.setdefault(txn.scheme_code, []).append(txn)

    # Scoped: this cache is rebuilt per snapshot, so an unqualified delete would
    # take the previous statement's rows with it. uq_user_mf_latest_snapshot_user_scheme
    # is widened to include cas_upload_id for the same reason.
    snapshot_id = await effective_scope(db, user_id)
    await db.execute(
        delete(UserMfLatestSnapshot).where(
            UserMfLatestSnapshot.user_id == user_id,
            *scope_filter(UserMfLatestSnapshot, snapshot_id),
        )
    )

    rows: list[UserMfLatestSnapshot] = []
    total_current_value = 0.0
    partial_values: dict[str, float] = {}

    for scheme_code, items in by_scheme.items():
        units = 0.0
        buy_units = 0.0  # units acquired — the weighted purchase-NAV denominator
        buy_cost = 0.0  # Σ(purchase amount) — the numerator
        cashflows: list[tuple[date, float]] = []
        for txn in items:
            # CAS stores redemption units as a *negative* number — use the magnitude so
            # the money-inflow (SELL) branch's ``units -= t_units`` actually removes units
            # instead of adding the negative back.
            t_units = abs(_f(txn.units))
            # Not abs(amount): a row whose amount column was mis-parsed is priced
            # off units x NAV instead, or a Rs 10 stamp duty becomes the cost
            # basis of a Rs 1.18L holding. See ``txn_value``.
            t_amt = trade_value(txn.units, txn.nav, txn.amount)
            if txn.transaction_type in _OUTFLOW_TYPES:
                units += t_units
                buy_units += t_units
                buy_cost += t_amt
                cashflows.append((txn.transaction_date, -t_amt))
            elif txn.transaction_type in _INFLOW_TYPES:
                # A sell removes units but NOT purchase history. Deducting the
                # redemption *proceeds* from the invested amount (what this did
                # until 2026-08-14) charges a profitable exit against the units
                # still held: HDFC Small Cap came out at invested = -658,135.88,
                # avg_nav = -157.9975, and an unrealised P&L larger than the
                # position. Cost basis is now derived after the loop instead.
                units -= t_units
                cashflows.append((txn.transaction_date, t_amt))

        # A fully-exited fund cancels to ~0 in float, not 0: DSP Midcap with
        # 2,591.249 units bought and sold left 4.5e-13. That is > 0, so a `<= 0`
        # test lets the crumb through and reports a closed fund as a live holding.
        if units <= _CLOSED_POSITION_UNITS:
            continue

        # Cost basis of the units still held, at the lifetime weighted purchase
        # NAV — the same definition ``holding_detail_service`` reads out, so the
        # holdings list and the fund detail page can no longer disagree. Scaling
        # with ``units`` is also what keeps avg_nav inside NUMERIC(12,4): it is a
        # purchase NAV, never a profit divided by a residual unit crumb.
        avg_cost = (buy_cost / buy_units) if buy_units > 0 else None
        invested = avg_cost * units if avg_cost is not None else 0.0

        meta = (
            await db.execute(
                select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code)
            )
        ).scalar_one_or_none()
        # Subgroup is classified live from the SEBI sub_category (centralised in
        # scheme_classification). The old MfFundRating.asset_subgroup column was
        # never populated, so reading it left every fund's sub_group empty.
        _, live_subgroup = (
            classify_holding(meta.sub_category, meta.scheme_name)
            if meta
            else (None, None)
        )
        # Start from today and walk back to the latest active NAV date.
        nav = await _nav_by_walking_back(db, scheme_code, date.today())
        if nav is None:
            # Fallback for sparse history (e.g., very old or backfilled schemes).
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
            nav_1y = await _nav_by_walking_back(
                db, scheme_code, nav.nav_date - timedelta(days=365)
            )
            nav_3y = await _nav_by_walking_back(
                db, scheme_code, nav.nav_date - timedelta(days=365 * 3)
            )
            nav_5y = await _nav_by_walking_back(
                db, scheme_code, nav.nav_date - timedelta(days=365 * 5)
            )
            if nav_1y is None:
                nav_1y = await _nav_on_or_before(
                    db, scheme_code, nav.nav_date - timedelta(days=365)
                )
            if nav_3y is None:
                nav_3y = await _nav_on_or_before(
                    db, scheme_code, nav.nav_date - timedelta(days=365 * 3)
                )
            if nav_5y is None:
                nav_5y = await _nav_on_or_before(
                    db, scheme_code, nav.nav_date - timedelta(days=365 * 5)
                )
            if nav_1y:
                one_y = (
                    ((_f(nav.nav) / _f(nav_1y.nav)) - 1.0) * 100.0
                    if _f(nav_1y.nav) > 0
                    else None
                )
            if nav_3y:
                three_y = _cagr_pct(_f(nav_3y.nav), _f(nav.nav), 3.0)
            if nav_5y:
                five_y = _cagr_pct(_f(nav_5y.nav), _f(nav.nav), 5.0)

        snap = UserMfLatestSnapshot(
            user_id=user_id,
            scheme_code=scheme_code,
            isin=(meta.isin if meta else None) or (items[-1].isin if items else None),
            fund_name=(meta.scheme_name if meta else None)
            or (items[-1].fund_name if items else None),
            amc_name=meta.amc_name if meta else None,
            category=(meta.category if meta else None)
            or (items[-1].category if items else None),
            sub_category=(meta.sub_category if meta else None)
            or (items[-1].sub_category if items else None),
            sub_group=live_subgroup or (items[-1].sub_group if items else None),
            invested_amount=round(invested, 2),
            current_units=round(units, 4),
            avg_nav=_nav_or_none(avg_cost, scheme_code),
            current_nav=_nav_or_none(curr_nav, scheme_code),
            current_value=round(curr_value, 2),
            unrealized_pnl=round(pnl, 2),
            absolute_return_pct=_pct_or_none(abs_pct, scheme_code),
            xirr_pct=_pct_or_none(xirr_pct, scheme_code),
            portfolio_weight_pct=None,
            return_1y_pct=_pct_or_none(one_y, scheme_code),
            return_3y_pct=_pct_or_none(three_y, scheme_code),
            return_5y_pct=_pct_or_none(five_y, scheme_code),
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
    if _commit:
        await db.commit()
    else:
        await db.flush()
    return len(rows)


async def rebuild_all_users_latest_snapshot(db: AsyncSession) -> tuple[int, int]:
    """Rebuild latest snapshot rows for every user who has MF transactions.

    Commits and expunges per user, so peak memory stays proportional to one
    user's data and a user whose rebuild raises is skipped rather than taking
    the run down with it.

    Returns:
        tuple[int, int]: (users_processed, total_snapshot_rows_written) — users
        that failed are excluded from the count and logged.
    """
    user_ids = list(
        (await db.execute(select(MfTransaction.user_id).distinct())).scalars().all()
    )
    users_processed = 0
    total_rows = 0
    failed = 0
    for user_id in user_ids:
        # One user must not be able to empty the table for everyone. Before this
        # guard a single NumericValueOutOfRange aborted the whole job, and the
        # snapshot went unwritten from 2026-05-07 to 2026-08-02 with no alert.
        # Rolling back discards the uncommitted batch, so commit per user and
        # accept the extra round-trips — correctness over throughput here.
        try:
            total_rows += await rebuild_user_latest_snapshot(db, user_id, _commit=False)
            await db.commit()
            users_processed += 1
        except Exception:
            await db.rollback()
            failed += 1
            logger.exception("latest-snapshot rebuild failed for user %s", user_id)
        db.expunge_all()

    if failed:
        logger.error(
            "latest-snapshot rebuild: %d of %d users failed and were skipped",
            failed,
            len(user_ids),
        )
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
