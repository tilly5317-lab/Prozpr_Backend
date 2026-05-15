"""Application service — `mf_holding_detail_service.py`.

Builds the payload for the *MF holding detail page* (``GET /mf/funds/{scheme_code}/holding-detail``):
scheme facts + NAV time series + the signed-in user's position and transaction
ledger in that scheme. Read-only — it never writes or fetches from upstream NAV
sources (use ``GET /mf/fund-metadata/{id}/investor-detail`` or the NAV-sync routes
for that).

Schemes can be addressed by AMFI **scheme code** *or* by **ISIN** — the portfolio
holdings rows produced by the CAMS CAS ingest store whichever of the two the
statement gave (AMFI preferred, ISIN fallback), so callers shouldn't have to know
which one they hold.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata, MfNavHistory, MfTransaction
from app.models.mf.enums import MfTransactionType
from app.models.portfolio import Portfolio, PortfolioHolding
from app.schemas.mf.holding_detail import (
    MfHoldingDetailResponse,
    MfHoldingNavPoint,
    MfHoldingPosition,
    MfHoldingTransactionItem,
)
from app.services.mf.nav_history_service import get_latest_nav_with_source_fallback
from app.services.mf_aa_normalizer import normalize_pending_imports

logger = logging.getLogger(__name__)

# Transaction types where units flow *into* the holding (colour green in the UI);
# everything else (SELL, SWITCH_OUT) flows out (colour red).
_INFLOW_TYPES: frozenset[MfTransactionType] = frozenset(
    {MfTransactionType.BUY, MfTransactionType.SWITCH_IN, MfTransactionType.DIVIDEND_REINVEST}
)

# Pull enough history for long-horizon charts + 5Y / max rolling NAV returns from ``mf_nav_history``.
_DEFAULT_NAV_LOOKBACK_DAYS = 365 * 10
_NAV_ROW_CAP = 8000  # safety cap on the series returned in one call
# If our stored NAV history for a scheme doesn't reach within this many days of today,
# treat it as "missing recent data" and trigger a refresh from mfapi.in.
# Set to 1 so the page always shows the latest available NAV (published by AMFI
# the previous evening) even before the 00:05 IST daily scheduler runs.
_RECENT_NAV_MAX_AGE_DAYS = 1
# A fund with real history should have hundreds of rows; fewer than this means
# a previous fetch was partial and we should re-fetch.
_MIN_NAV_ROWS_FOR_CHART = 180

_ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")


def _f(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _as_optional_float(v: object) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _looks_like_isin(code: str) -> bool:
    return bool(_ISIN_RE.match(code.strip().upper()))


def _first_nav_on_or_after(sorted_rows: list[MfNavHistory], cutoff: date) -> Optional[float]:
    """First NAV observation on or after ``cutoff`` (sorted ascending by date)."""
    for r in sorted_rows:
        if r.nav_date >= cutoff:
            n = _f(r.nav)
            if n is not None and n > 0:
                return n
    return None


def _nav_at_or_before(sorted_rows: list[MfNavHistory], cutoff: date) -> Optional[float]:
    """Last NAV observation in ``sorted_rows`` (ascending by date) on or before ``cutoff``."""
    chosen: Optional[float] = None
    for r in sorted_rows:
        if r.nav_date <= cutoff:
            chosen = _f(r.nav)
        else:
            break
    if chosen is None or chosen <= 0:
        return None
    return chosen


def _compute_nav_returns_pct(nav_rows: list[MfNavHistory]) -> dict[str, Optional[float | date]]:
    """Rolling returns from ``mf_nav_history``: end = latest row; start = NAV at or before horizon cutoffs."""
    empty: dict[str, Optional[float | date]] = {
        "nav_returns_as_of": None,
        "nav_return_ytd_pct": None,
        "nav_return_6m_pct": None,
        "nav_return_1y_pct": None,
        "nav_return_3y_pct": None,
        "nav_return_5y_pct": None,
    }
    if not nav_rows:
        return empty

    sorted_rows = sorted(nav_rows, key=lambda r: r.nav_date)
    last_row = sorted_rows[-1]
    last_date = last_row.nav_date
    end_nav = _f(last_row.nav)
    if end_nav is None or end_nav <= 0:
        return empty

    def pct_since(cutoff: date) -> Optional[float]:
        start_nav = _nav_at_or_before(sorted_rows, cutoff)
        if start_nav is None:
            return None
        return round((end_nav / start_nav - 1) * 100, 2)

    ytd_anchor = date(last_date.year, 1, 1)
    start_ytd = _first_nav_on_or_after(sorted_rows, ytd_anchor)
    nav_return_ytd_pct: Optional[float] = None
    if start_ytd is not None:
        nav_return_ytd_pct = round((end_nav / start_ytd - 1) * 100, 2)

    out: dict[str, Optional[float | date]] = {
        "nav_returns_as_of": last_date,
        "nav_return_ytd_pct": nav_return_ytd_pct,
        "nav_return_6m_pct": pct_since(last_date - timedelta(days=182)),
        "nav_return_1y_pct": pct_since(last_date - timedelta(days=365)),
        "nav_return_3y_pct": pct_since(last_date - timedelta(days=365 * 3)),
        "nav_return_5y_pct": pct_since(last_date - timedelta(days=365 * 5)),
    }
    return out


async def _resolve_metadata(db: AsyncSession, code: str) -> Optional[MfFundMetadata]:
    """Find the fund-metadata row for an AMFI scheme code or an ISIN."""
    row = (
        await db.execute(select(MfFundMetadata).where(MfFundMetadata.scheme_code == code))
    ).scalar_one_or_none()
    if row is not None:
        return row
    if _looks_like_isin(code):
        return (
            await db.execute(
                select(MfFundMetadata)
                .where(MfFundMetadata.isin == code.strip().upper())
                .order_by(MfFundMetadata.is_active.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return None


async def _latest_nav(db: AsyncSession, scheme_code: str) -> Optional[MfNavHistory]:
    return (
        await db.execute(
            select(MfNavHistory)
            .where(MfNavHistory.scheme_code == scheme_code)
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nav_series(
    db: AsyncSession, scheme_code: str, *, date_from: date, date_to: date
) -> tuple[list[MfNavHistory], bool]:
    rows = list(
        (
            await db.execute(
                select(MfNavHistory)
                .where(
                    MfNavHistory.scheme_code == scheme_code,
                    MfNavHistory.nav_date >= date_from,
                    MfNavHistory.nav_date <= date_to,
                )
                .order_by(MfNavHistory.nav_date.asc())
                .limit(_NAV_ROW_CAP + 1)
            )
        ).scalars().all()
    )
    truncated = len(rows) > _NAV_ROW_CAP
    return rows[:_NAV_ROW_CAP], truncated


async def _user_transactions(
    db: AsyncSession, user_id: uuid.UUID, scheme_codes: set[str]
) -> list[MfTransaction]:
    if not scheme_codes:
        return []
    return list(
        (
            await db.execute(
                select(MfTransaction)
                .where(
                    MfTransaction.user_id == user_id,
                    MfTransaction.scheme_code.in_(scheme_codes),
                )
                .order_by(MfTransaction.transaction_date.asc(), MfTransaction.created_at.asc())
            )
        ).scalars().all()
    )


async def _user_position(
    db: AsyncSession, user_id: uuid.UUID, *, scheme_code: str, isin: Optional[str]
) -> Optional[MfHoldingPosition]:
    primary = (
        await db.execute(
            select(Portfolio)
            .where(Portfolio.user_id == user_id, Portfolio.is_primary.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if primary is None:
        return None

    tickers = {t for t in (scheme_code, isin) if t}
    holdings = list(
        (
            await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.portfolio_id == primary.id,
                    PortfolioHolding.ticker_symbol.in_(tickers),
                )
            )
        ).scalars().all()
    )
    if not holdings:
        return None

    total_units = 0.0
    total_value = 0.0
    total_alloc = 0.0
    weighted_cost_num = 0.0  # Σ average_cost * units  (for a units-weighted average)
    weighted_cost_den = 0.0
    current_price: Optional[float] = None
    for h in holdings:
        units = _f(h.quantity) or 0.0
        total_units += units
        total_value += _f(h.current_value) or 0.0
        total_alloc += _f(h.allocation_percentage) or 0.0
        avg_cost = _f(h.average_cost)
        if avg_cost is not None and units > 0:
            weighted_cost_num += avg_cost * units
            weighted_cost_den += units
        if current_price is None and h.current_price is not None:
            current_price = _f(h.current_price)

    average_cost = (weighted_cost_num / weighted_cost_den) if weighted_cost_den > 0 else None
    invested = (average_cost * total_units) if (average_cost is not None and total_units > 0) else None
    gain = (total_value - invested) if invested is not None else None
    gain_pct = (100.0 * gain / invested) if (gain is not None and invested and invested > 0) else None

    return MfHoldingPosition(
        units=round(total_units, 4) if total_units else None,
        average_cost=round(average_cost, 4) if average_cost is not None else None,
        current_price=round(current_price, 4) if current_price is not None else None,
        current_value=round(total_value, 2) if total_value else None,
        allocation_percentage=round(total_alloc, 2) if total_alloc else None,
        invested_amount=round(invested, 2) if invested is not None else None,
        unrealised_gain=round(gain, 2) if gain is not None else None,
        unrealised_gain_pct=round(gain_pct, 2) if gain_pct is not None else None,
        folios=len(holdings),
    )


def _to_txn_item(t: MfTransaction) -> MfHoldingTransactionItem:
    is_inflow = t.transaction_type in _INFLOW_TYPES
    amount = _f(t.amount) or 0.0
    signed = abs(amount) if is_inflow else -abs(amount)
    return MfHoldingTransactionItem(
        id=t.id,
        transaction_date=t.transaction_date,
        transaction_type=t.transaction_type,
        folio_number=t.folio_number,
        units=_f(t.units) or 0.0,
        nav=_f(t.nav) or 0.0,
        amount=amount,
        stamp_duty=_f(t.stamp_duty),
        source_system=t.source_system,
        is_inflow=is_inflow,
        signed_amount=round(signed, 2),
    )


async def build_holding_detail(
    db: AsyncSession,
    user_id: uuid.UUID,
    code: str,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> MfHoldingDetailResponse:
    """Assemble the holding-detail payload for ``code`` (AMFI scheme code or ISIN)."""
    code = code.strip()
    meta = await _resolve_metadata(db, code)

    # The AMFI scheme code is the join key for NAV history and the normalized ledger.
    scheme_code = meta.scheme_code if meta is not None else code
    isin = meta.isin if meta is not None else (code if _looks_like_isin(code) else None)

    notes: list[str] = []

    today = date.today()
    d_to = date_to or today
    d_from = date_from or (d_to - timedelta(days=_DEFAULT_NAV_LOOKBACK_DAYS))
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    latest = await _latest_nav(db, scheme_code)
    nav_rows, truncated = await _nav_series(db, scheme_code, date_from=d_from, date_to=d_to)

    # Refresh from mfapi.in when NAV history is absent, too thin for a chart,
    # or stale (newest point older than 1 day).  On first visit this pulls the
    # full series from inception; on subsequent visits only the delta is inserted.
    recent_cutoff = today - timedelta(days=_RECENT_NAV_MAX_AGE_DAYS)
    needs_nav_backfill = (
        latest is None
        or not nav_rows
        or latest.nav_date < recent_cutoff
        or len(nav_rows) < _MIN_NAV_ROWS_FOR_CHART
    )
    if needs_nav_backfill and not _looks_like_isin(scheme_code):
        try:
            await get_latest_nav_with_source_fallback(db, scheme_code)
        except Exception:  # noqa: BLE001 — network/parse failure; a note is added below
            notes.append(
                "Couldn't reach mfapi.in to backfill NAV history for this scheme; "
                "the chart shows whatever NAVs are already stored."
            )
        else:
            latest = await _latest_nav(db, scheme_code)
            nav_rows, truncated = await _nav_series(db, scheme_code, date_from=d_from, date_to=d_to)

    if latest is None:
        notes.append(
            "No NAV history is stored for this scheme yet — trigger a NAV sync to populate the chart."
        )
    elif not nav_rows:
        notes.append(
            f"No NAV points between {d_from.isoformat()} and {d_to.isoformat()}; "
            f"the latest stored NAV is from {latest.nav_date.isoformat()}."
        )

    # Self-heal: if the user has CAS imports that were received but never normalized
    # (or whose normalization previously failed), run them now so their transactions
    # show up here instead of staying invisible until someone hits the ingest route.
    try:
        await normalize_pending_imports(db, user_id)
    except Exception:  # noqa: BLE001 — best-effort; never block the page on this
        logger.exception("normalize_pending_imports failed for user %s during holding-detail", user_id)

    # The NAV backfill and the normalization above may have just created the
    # ``mf_fund_metadata`` row for this scheme — pick it up so we can use its
    # name / AMC / category instead of falling back to the transaction rows.
    if meta is None:
        meta = await _resolve_metadata(db, code)
        if meta is not None:
            scheme_code = meta.scheme_code
            isin = meta.isin

    # Pull the user's ledger by AMFI code and (defensively) by ISIN, since older
    # rows may have been stored under the ISIN when the AMFI code was unknown.
    txn_codes = {scheme_code}
    if isin:
        txn_codes.add(isin)
    txns = await _user_transactions(db, user_id, txn_codes)
    if not txns:
        notes.append("You have no recorded transactions in this scheme.")

    position = await _user_position(db, user_id, scheme_code=scheme_code, isin=isin)

    returns_map = _compute_nav_returns_pct(nav_rows)
    _asof = returns_map.get("nav_returns_as_of")
    nav_returns_as_of = _asof if isinstance(_asof, date) else None

    return MfHoldingDetailResponse(
        scheme_code=scheme_code,
        scheme_name=(meta.scheme_name if meta else (txns[0].fund_name if txns else None)),
        amc_name=(meta.amc_name if meta else None),
        category=(meta.category if meta else (txns[0].category if txns else None)),
        sub_category=(meta.sub_category if meta else (txns[0].sub_category if txns else None)),
        isin=isin or (txns[0].isin if txns else None),
        plan_type=(meta.plan_type.value if meta and meta.plan_type else None),
        option_type=(meta.option_type.value if meta and meta.option_type else None),
        metadata_id=(meta.id if meta else None),
        latest_nav=_f(latest.nav) if latest else None,
        latest_nav_date=latest.nav_date if latest else None,
        nav_history=[MfHoldingNavPoint(nav_date=r.nav_date, nav=_f(r.nav) or 0.0) for r in nav_rows],
        nav_history_from=(nav_rows[0].nav_date if nav_rows else None),
        nav_history_to=(nav_rows[-1].nav_date if nav_rows else None),
        nav_history_truncated=truncated,
        nav_returns_as_of=nav_returns_as_of,
        nav_return_ytd_pct=_as_optional_float(returns_map.get("nav_return_ytd_pct")),
        nav_return_6m_pct=_as_optional_float(returns_map.get("nav_return_6m_pct")),
        nav_return_1y_pct=_as_optional_float(returns_map.get("nav_return_1y_pct")),
        nav_return_3y_pct=_as_optional_float(returns_map.get("nav_return_3y_pct")),
        nav_return_5y_pct=_as_optional_float(returns_map.get("nav_return_5y_pct")),
        position=position,
        transactions=[_to_txn_item(t) for t in txns],
        notes=notes,
    )
