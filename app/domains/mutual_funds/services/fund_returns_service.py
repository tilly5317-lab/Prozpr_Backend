"""Trailing CAGR for a scheme from stored NAV history.

DB-only — reads ``mf_nav_history`` and never calls mfapi.in. A horizon with no
stored NAV at/before its cutoff returns ``None`` (history completeness is the
NAV scheduler's job, out of band, not this flow's).
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory

ensure_ai_agents_path()
from financial_primitives import cagr  # noqa: E402

_HORIZONS: tuple[tuple[str, int], ...] = (
    ("return_1y_cagr_pct", 1),
    ("return_3y_cagr_pct", 3),
    ("return_5y_cagr_pct", 5),
)


async def trailing_cagr_for_scheme(
    db: AsyncSession, scheme_code: str
) -> dict[str, Optional[float]]:
    """Annualized 1y/3y/5y return for a scheme from stored NAV. Missing → ``None``."""
    rows = (
        await db.execute(
            select(MfNavHistory.nav, MfNavHistory.nav_date)
            .where(MfNavHistory.scheme_code == scheme_code)
            .order_by(MfNavHistory.nav_date)
        )
    ).all()
    out: dict[str, Optional[float]] = {key: None for key, _ in _HORIZONS}
    if not rows:
        return out
    end_nav = float(rows[-1].nav)
    last_date = rows[-1].nav_date
    for key, years in _HORIZONS:
        cutoff = last_date - dt.timedelta(days=365 * years)
        start = next((float(r.nav) for r in reversed(rows) if r.nav_date <= cutoff), None)
        if start is not None:
            out[key] = cagr(start, end_nav, float(years))
    return out
