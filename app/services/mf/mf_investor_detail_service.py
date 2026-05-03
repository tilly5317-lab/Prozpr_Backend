"""NAV-based return metrics and chart data for fund detail (investor) pages."""

from __future__ import annotations

import math
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata, MfNavHistory
from app.schemas.mf.fund_metadata import (
    MfFundInvestorDetailResponse,
    MfMetadataReturnsSnapshot,
    MfNavChartPoint,
    MfNavDerivedReturns,
)

CHART_LOOKBACK_DAYS = 800
CHART_MAX_POINTS = 120
NAV_QUERY_CAP = 2500


def _to_float(nav: object) -> float:
    if isinstance(nav, Decimal):
        return float(nav)
    return float(nav)


def _years_between(d0: date, d1: date) -> float:
    if d1 <= d0:
        return 0.0
    return (d1 - d0).days / 365.25


def _abs_return_pct(nav_start: float, nav_end: float) -> float:
    if nav_start <= 0 or nav_end <= 0:
        raise ValueError("NAV must be positive")
    return (nav_end / nav_start - 1.0) * 100.0


def _cagr_pct(nav_start: float, nav_end: float, years: float) -> Optional[float]:
    if nav_start <= 0 or nav_end <= 0 or years <= 0:
        return None
    try:
        return (pow(nav_end / nav_start, 1.0 / years) - 1.0) * 100.0
    except (ValueError, OverflowError):
        return None


async def _nav_on_or_before(
    db: AsyncSession, scheme_code: str, target: date
) -> Optional[MfNavHistory]:
    stmt = (
        select(MfNavHistory)
        .where(
            MfNavHistory.scheme_code == scheme_code,
            MfNavHistory.nav_date <= target,
        )
        .order_by(MfNavHistory.nav_date.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _latest_nav(db: AsyncSession, scheme_code: str) -> Optional[MfNavHistory]:
    stmt = (
        select(MfNavHistory)
        .where(MfNavHistory.scheme_code == scheme_code)
        .order_by(MfNavHistory.nav_date.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _first_nav(db: AsyncSession, scheme_code: str) -> Optional[MfNavHistory]:
    stmt = (
        select(MfNavHistory)
        .where(MfNavHistory.scheme_code == scheme_code)
        .order_by(MfNavHistory.nav_date.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _nav_row_count(db: AsyncSession, scheme_code: str) -> int:
    stmt = select(func.count()).select_from(MfNavHistory).where(MfNavHistory.scheme_code == scheme_code)
    return int((await db.execute(stmt)).scalar() or 0)


async def _fetch_chart_rows(
    db: AsyncSession, scheme_code: str, earliest: date
) -> List[MfNavHistory]:
    stmt = (
        select(MfNavHistory)
        .where(
            MfNavHistory.scheme_code == scheme_code,
            MfNavHistory.nav_date >= earliest,
        )
        .order_by(MfNavHistory.nav_date.asc())
        .limit(NAV_QUERY_CAP)
    )
    return list((await db.execute(stmt)).scalars().all())


def _downsample_chart(rows: List[MfNavHistory], max_points: int) -> List[MfNavChartPoint]:
    if not rows:
        return []
    if len(rows) <= max_points:
        return [MfNavChartPoint(nav_date=r.nav_date, nav=_to_float(r.nav)) for r in rows]
    step = max(1, math.ceil(len(rows) / max_points))
    sampled = rows[::step]
    last = rows[-1]
    if sampled[-1].nav_date != last.nav_date:
        sampled = sampled[:-1] + [last]
    return [MfNavChartPoint(nav_date=r.nav_date, nav=_to_float(r.nav)) for r in sampled]


def _rolling_return(
    latest: MfNavHistory,
    start_row: Optional[MfNavHistory],
    min_span_days: int,
) -> Tuple[Optional[float], Optional[str]]:
    if not start_row:
        return None, "No NAV on or before the comparison date."
    span = (latest.nav_date - start_row.nav_date).days
    if span < min_span_days:
        return None, f"Only {span} days of overlap; need at least {min_span_days}."
    try:
        return _abs_return_pct(_to_float(start_row.nav), _to_float(latest.nav)), None
    except ValueError:
        return None, "Invalid NAV values for return calculation."


def _cagr_window(
    latest: MfNavHistory,
    start_row: Optional[MfNavHistory],
    min_span_days: int,
) -> Tuple[Optional[float], Optional[str]]:
    if not start_row:
        return None, "No NAV on or before the comparison date."
    span_days = (latest.nav_date - start_row.nav_date).days
    if span_days < min_span_days:
        return None, f"History too short for this horizon ({span_days} days)."
    years = _years_between(start_row.nav_date, latest.nav_date)
    cagr = _cagr_pct(_to_float(start_row.nav), _to_float(latest.nav), years)
    if cagr is None:
        return None, "Could not compute CAGR."
    return cagr, None


async def build_investor_detail(db: AsyncSession, metadata_id: uuid.UUID) -> MfFundInvestorDetailResponse:
    meta = (
        await db.execute(select(MfFundMetadata).where(MfFundMetadata.id == metadata_id))
    ).scalar_one_or_none()
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund metadata not found")

    scheme = meta.scheme_code
    latest = await _latest_nav(db, scheme)
    first = await _first_nav(db, scheme)
    n_points = await _nav_row_count(db, scheme)

    disclaimers: List[str] = []

    returns = MfNavDerivedReturns(
        return_1y_abs_pct=None,
        return_3y_cagr_pct=None,
        return_5y_cagr_pct=None,
        return_10y_cagr_pct=None,
        return_inception_abs_pct=None,
        return_inception_cagr_pct=None,
        first_nav_date=first.nav_date if first else None,
        latest_nav=_to_float(latest.nav) if latest else None,
        latest_nav_date=latest.nav_date if latest else None,
        nav_row_count=n_points,
    )

    meta_snap = MfMetadataReturnsSnapshot(
        returns_1y_pct=float(meta.returns_1y_pct) if meta.returns_1y_pct is not None else None,
        returns_3y_pct=float(meta.returns_3y_pct) if meta.returns_3y_pct is not None else None,
        returns_5y_pct=float(meta.returns_5y_pct) if meta.returns_5y_pct is not None else None,
        returns_10y_pct=float(meta.returns_10y_pct) if meta.returns_10y_pct is not None else None,
    )

    chart: List[MfNavChartPoint] = []

    if not latest:
        disclaimers.append("No NAV history is stored for this scheme yet. Sync NAV data to see performance.")
        return MfFundInvestorDetailResponse(
            metadata_id=meta.id,
            scheme_code=meta.scheme_code,
            scheme_name=meta.scheme_name,
            amc_name=meta.amc_name,
            category=meta.category,
            sub_category=meta.sub_category,
            isin=meta.isin,
            isin_div_reinvest=meta.isin_div_reinvest,
            plan_type=meta.plan_type,
            option_type=meta.option_type,
            is_active=meta.is_active,
            risk_rating_sebi=meta.risk_rating_sebi,
            asset_class=meta.asset_class,
            asset_subgroup=meta.asset_subgroup,
            direct_plan_fees=float(meta.direct_plan_fees) if meta.direct_plan_fees is not None else None,
            regular_plan_fees=float(meta.regular_plan_fees) if meta.regular_plan_fees is not None else None,
            exit_load_percent=float(meta.exit_load_percent) if meta.exit_load_percent is not None else None,
            exit_load_months=meta.exit_load_months,
            large_cap_equity_pct=float(meta.large_cap_equity_pct) if meta.large_cap_equity_pct is not None else None,
            mid_cap_equity_pct=float(meta.mid_cap_equity_pct) if meta.mid_cap_equity_pct is not None else None,
            small_cap_equity_pct=float(meta.small_cap_equity_pct) if meta.small_cap_equity_pct is not None else None,
            debt_pct=float(meta.debt_pct) if meta.debt_pct is not None else None,
            others_pct=float(meta.others_pct) if meta.others_pct is not None else None,
            returns_from_nav=returns,
            returns_from_metadata=meta_snap,
            nav_chart=chart,
            disclaimers=disclaimers,
        )

    # Rolling horizons — anchor dates from latest published NAV date (not wall-clock “today”).
    end_d = latest.nav_date
    one_y_start = await _nav_on_or_before(db, scheme, end_d - timedelta(days=365))
    three_y_start = await _nav_on_or_before(db, scheme, end_d - timedelta(days=365 * 3))
    five_y_start = await _nav_on_or_before(db, scheme, end_d - timedelta(days=365 * 5))
    ten_y_start = await _nav_on_or_before(db, scheme, end_d - timedelta(days=365 * 10))

    r1, m1 = _rolling_return(latest, one_y_start, min_span_days=300)
    returns.return_1y_abs_pct = r1
    if m1 and r1 is None:
        disclaimers.append(f"1Y return: {m1}")

    c3, e3 = _cagr_window(latest, three_y_start, min_span_days=1000)
    returns.return_3y_cagr_pct = c3
    if e3 and c3 is None:
        disclaimers.append(f"3Y CAGR: {e3}")

    c5, e5 = _cagr_window(latest, five_y_start, min_span_days=1700)
    returns.return_5y_cagr_pct = c5
    if e5 and c5 is None:
        disclaimers.append(f"5Y CAGR: {e5}")

    c10, e10 = _cagr_window(latest, ten_y_start, min_span_days=3300)
    returns.return_10y_cagr_pct = c10
    if e10 and c10 is None:
        disclaimers.append(f"10Y CAGR: {e10}")

    if first and first.nav_date < latest.nav_date:
        span_incept = (latest.nav_date - first.nav_date).days
        if span_incept >= 30:
            try:
                returns.return_inception_abs_pct = _abs_return_pct(
                    _to_float(first.nav), _to_float(latest.nav)
                )
                years_i = _years_between(first.nav_date, latest.nav_date)
                returns.return_inception_cagr_pct = _cagr_pct(
                    _to_float(first.nav), _to_float(latest.nav), years_i
                )
            except ValueError:
                disclaimers.append("Could not compute since-inception returns from NAV.")
        else:
            disclaimers.append("Since inception: NAV history is too short for a meaningful trend.")

    chart_from = end_d - timedelta(days=CHART_LOOKBACK_DAYS)
    raw_chart = await _fetch_chart_rows(db, scheme, chart_from)
    chart = _downsample_chart(raw_chart, CHART_MAX_POINTS)
    if not chart:
        disclaimers.append("Not enough NAV points in the selected window for a performance chart.")

    return MfFundInvestorDetailResponse(
        metadata_id=meta.id,
        scheme_code=meta.scheme_code,
        scheme_name=meta.scheme_name,
        amc_name=meta.amc_name,
        category=meta.category,
        sub_category=meta.sub_category,
        isin=meta.isin,
        isin_div_reinvest=meta.isin_div_reinvest,
        plan_type=meta.plan_type,
        option_type=meta.option_type,
        is_active=meta.is_active,
        risk_rating_sebi=meta.risk_rating_sebi,
        asset_class=meta.asset_class,
        asset_subgroup=meta.asset_subgroup,
        direct_plan_fees=float(meta.direct_plan_fees) if meta.direct_plan_fees is not None else None,
        regular_plan_fees=float(meta.regular_plan_fees) if meta.regular_plan_fees is not None else None,
        exit_load_percent=float(meta.exit_load_percent) if meta.exit_load_percent is not None else None,
        exit_load_months=meta.exit_load_months,
        large_cap_equity_pct=float(meta.large_cap_equity_pct) if meta.large_cap_equity_pct is not None else None,
        mid_cap_equity_pct=float(meta.mid_cap_equity_pct) if meta.mid_cap_equity_pct is not None else None,
        small_cap_equity_pct=float(meta.small_cap_equity_pct) if meta.small_cap_equity_pct is not None else None,
        debt_pct=float(meta.debt_pct) if meta.debt_pct is not None else None,
        others_pct=float(meta.others_pct) if meta.others_pct is not None else None,
        returns_from_nav=returns,
        returns_from_metadata=meta_snap,
        nav_chart=chart,
        disclaimers=disclaimers,
    )
