"""Whole-universe 'best performing funds' screen from stored NAV history.

DB-only — reads ``mf_fund_metadata`` + ``mf_nav_history``, never calls mfapi.in.
Ranks REGULAR-GROWTH schemes (one row per underlying fund) by trailing CAGR over
a horizon, measured against a single common ``as_of`` date so every fund is
compared over the same window. A scheme lacking NAV at either endpoint drops out.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.models.enums import MfOptionType, MfPlanType
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory

ensure_ai_agents_path()
from financial_primitives import cagr  # noqa: E402

# A fund whose latest NAV lags the freshest in the universe by more than this is
# treated as stale (stopped reporting / delisted) and excluded — ranking it on an
# old NAV over a window that nominally ends "today" would be misleading.
_FRESHNESS_DAYS = 30


@dataclass(frozen=True)
class ScreenedFund:
    scheme_code: str
    scheme_name: str
    amc_name: str
    sub_category: Optional[str]
    horizon_years: int
    return_cagr_pct: float


def _latest_leg_on_or_before(bound: dt.date):
    """Subquery: the latest NAV per scheme with ``nav_date <= bound``."""
    rn = func.row_number().over(
        partition_by=MfNavHistory.scheme_code,
        order_by=MfNavHistory.nav_date.desc(),
    ).label("rn")
    ranked = (
        select(
            MfNavHistory.scheme_code.label("scheme_code"),
            MfNavHistory.nav.label("nav"),
            MfNavHistory.nav_date.label("nav_date"),
            rn,
        )
        .where(MfNavHistory.nav_date <= bound)
        .subquery()
    )
    return (
        select(
            ranked.c.scheme_code, ranked.c.nav, ranked.c.nav_date
        )
        .where(ranked.c.rn == 1)
        .subquery()
    )


async def screen_top_funds(
    db: AsyncSession,
    *,
    horizon_years: int = 3,
    category: Optional[str] = None,
    limit: int = 5,
) -> list[ScreenedFund]:
    """Top-``limit`` REGULAR-GROWTH funds by trailing ``horizon_years`` CAGR."""
    as_of = (await db.execute(select(func.max(MfNavHistory.nav_date)))).scalar_one_or_none()
    if as_of is None:
        return []
    start_cutoff = as_of - dt.timedelta(days=365 * horizon_years)
    fresh_cutoff = as_of - dt.timedelta(days=_FRESHNESS_DAYS)

    end_leg = _latest_leg_on_or_before(as_of)
    start_leg = _latest_leg_on_or_before(start_cutoff)

    query = (
        select(
            MfFundMetadata.scheme_code,
            MfFundMetadata.scheme_name,
            MfFundMetadata.amc_name,
            MfFundMetadata.sub_category,
            start_leg.c.nav.label("start_nav"),
            end_leg.c.nav.label("end_nav"),
        )
        .join(end_leg, end_leg.c.scheme_code == MfFundMetadata.scheme_code)
        .join(start_leg, start_leg.c.scheme_code == MfFundMetadata.scheme_code)
        .where(MfFundMetadata.plan_type == MfPlanType.REGULAR)
        .where(MfFundMetadata.option_type == MfOptionType.GROWTH)
        .where(MfFundMetadata.is_active.is_(True))
        # Backstop: the source feed's plan_type is unreliable — some Direct-plan
        # schemes carry plan_type=REGULAR, which both leaks a Direct plan and
        # duplicates the underlying fund. The scheme name is the reliable signal.
        .where(~MfFundMetadata.scheme_name.ilike("%direct%"))
        .where(end_leg.c.nav_date >= fresh_cutoff)
    )
    if category:
        query = query.where(MfFundMetadata.sub_category == category)

    rows = (await db.execute(query)).all()

    scored: list[ScreenedFund] = []
    for r in rows:
        start_nav = float(r.start_nav)
        end_nav = float(r.end_nav)
        if start_nav <= 0:
            continue
        scored.append(
            ScreenedFund(
                scheme_code=r.scheme_code,
                scheme_name=r.scheme_name,
                amc_name=r.amc_name,
                sub_category=r.sub_category,
                horizon_years=horizon_years,
                return_cagr_pct=cagr(start_nav, end_nav, float(horizon_years)),
            )
        )

    scored.sort(key=lambda f: f.return_cagr_pct, reverse=True)
    return scored[:limit]
