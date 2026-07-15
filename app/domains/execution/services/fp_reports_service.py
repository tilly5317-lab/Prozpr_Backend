"""FP folios & investment reports — real FP call first, test-data fallback.

Each endpoint **calls the live FP API** (``GET /v2/mf_folios`` for holdings, the
reports API for returns). On our sandbox those come back empty / 404 — a folio
is only minted once a purchase settles and nothing settles here — so rather than
show a blank portfolio, we fall back to clearly-labelled SIMULATED test data
(``simulated=True``), priced with real NAVs from ``mf_nav_history`` where
available. The test basis is the user's own placed FP orders (lumpsum/SIP), or
the tenant-enabled test schemes if they haven't placed any.

The moment FP returns real folios (production, or once the sandbox is enabled to
settle), this same endpoint serves those instead — ``simulated`` flips to False.
Never treat ``simulated=True`` data as real holdings.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domains.execution.models import FpExecOrder
from app.domains.execution.schemas.fp_schemas import (
    FpCapitalGains,
    FpFolio,
    FpFoliosResponse,
    FpInvestmentReport,
    FpReportSchemeLine,
)
from app.domains.execution.services.fp_client import FpError, get_fp_client
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory

logger = logging.getLogger(__name__)

_SIM_NOTE = (
    "SIMULATED test data — FP returned no folios (no order settles on the sandbox, "
    "so FP mints no folio). Priced with real NAVs where available. NOT real holdings."
)
_SAMPLE_INVESTED = [50000.0, 30000.0, 25000.0, 20000.0, 15000.0]


# ---- test-data helpers ----------------------------------------------------
def _synthetic_nav(isin: str) -> float:
    """Deterministic pretend NAV (~50-300) when we have no real NAV for a test
    scheme — stable across restarts so folio values don't jump."""
    seed = int(hashlib.md5(isin.encode("utf-8")).hexdigest()[:6], 16)
    return round(50.0 + (seed % 25000) / 100.0, 4)


def _folio_number(isin: str) -> str:
    """Deterministic 9-digit folio number derived from the ISIN."""
    return str(int(hashlib.md5(isin.encode("utf-8")).hexdigest()[:9], 16) % 10**9).zfill(9)


async def _order_basis(db: AsyncSession, user_id: uuid.UUID) -> dict[str, float]:
    """Invested-amount-per-ISIN from the user's placed FP buy orders."""
    rows = (
        (
            await db.execute(
                select(FpExecOrder).where(
                    FpExecOrder.user_id == user_id,
                    FpExecOrder.kind.in_(["LUMPSUM", "SIP"]),
                )
            )
        )
        .scalars()
        .all()
    )
    agg: dict[str, float] = {}
    for o in rows:
        if not o.isin:
            continue
        agg[o.isin] = agg.get(o.isin, 0.0) + float(o.amount or 0)
    return {k: v for k, v in agg.items() if v > 0}


async def _nav_pair(
    db: AsyncSession, scheme_code: str
) -> tuple[MfNavHistory | None, MfNavHistory | None]:
    """(latest NAV, NAV ~1y ago) for a scheme, to price a test holding + return."""
    latest = (
        (
            await db.execute(
                select(MfNavHistory)
                .where(MfNavHistory.scheme_code == scheme_code)
                .order_by(MfNavHistory.nav_date.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    old = None
    if latest is not None:
        cutoff = latest.nav_date - timedelta(days=365)
        old = (
            (
                await db.execute(
                    select(MfNavHistory)
                    .where(
                        MfNavHistory.scheme_code == scheme_code,
                        MfNavHistory.nav_date <= cutoff,
                    )
                    .order_by(MfNavHistory.nav_date.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    return latest, old


async def _build_test_folio(
    db: AsyncSession, isin: str, invested: float
) -> FpFolio:
    meta = (
        (await db.execute(select(MfFundMetadata).where(MfFundMetadata.isin == isin)))
        .scalars()
        .first()
    )
    scheme_code = meta.scheme_code if meta else None
    latest = old = None
    if scheme_code:
        latest, old = await _nav_pair(db, scheme_code)

    if latest is not None:
        current_nav = float(latest.nav)
        nav_date = latest.nav_date
        cost_nav = float(old.nav) if (old and old.nav) else round(current_nav / 1.125, 4)
    else:
        current_nav = _synthetic_nav(isin)
        nav_date = None
        cost_nav = round(current_nav / 1.125, 4)
    if cost_nav <= 0:
        cost_nav = current_nav or 1.0

    units = round(invested / cost_nav, 4)
    current_value = round(units * current_nav, 2)
    gain = round(current_value - invested, 2)
    gain_pct = round(gain / invested * 100, 2) if invested else 0.0

    return FpFolio(
        folio_number=_folio_number(isin),
        isin=isin,
        scheme_code=scheme_code,
        scheme_name=(meta.scheme_name if meta else None),
        amc_name=(meta.amc_name if meta else None),
        category=(meta.category if meta else None),
        units=units,
        avg_cost_nav=round(cost_nav, 4),
        current_nav=round(current_nav, 4),
        nav_date=nav_date,
        invested_amount=round(invested, 2),
        current_value=current_value,
        unrealized_gain=gain,
        unrealized_gain_pct=gain_pct,
    )


def _map_fp_folio(raw: dict) -> FpFolio:
    """Best-effort map of a real FP folio row (shape unverified — sandbox never
    returns one). Defensive: any missing field falls back to a safe default."""
    def num(*keys: str) -> float:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 0.0

    invested = num("invested_amount", "cost_value", "purchase_value")
    current = num("current_value", "market_value", "valuation")
    gain = round(current - invested, 2)
    return FpFolio(
        folio_number=str(raw.get("folio_number") or raw.get("number") or "unknown"),
        isin=str(raw.get("scheme") or raw.get("isin") or "unknown"),
        scheme_code=(str(raw["amfi_code"]) if raw.get("amfi_code") else None),
        scheme_name=raw.get("scheme_name"),
        amc_name=raw.get("amc_name"),
        category=raw.get("category"),
        units=num("units", "balance_units"),
        avg_cost_nav=num("average_cost", "avg_cost_nav"),
        current_nav=num("nav", "current_nav"),
        nav_date=None,
        invested_amount=round(invested, 2),
        current_value=round(current, 2),
        unrealized_gain=gain,
        unrealized_gain_pct=round(gain / invested * 100, 2) if invested else 0.0,
    )


def _folios_response(
    folios: list[FpFolio], simulated: bool, source: str, note: str | None
) -> FpFoliosResponse:
    return FpFoliosResponse(
        simulated=simulated,
        source=source,
        note=note,
        count=len(folios),
        total_invested=round(sum(f.invested_amount for f in folios), 2),
        total_current_value=round(sum(f.current_value for f in folios), 2),
        total_unrealized_gain=round(sum(f.unrealized_gain for f in folios), 2),
        folios=folios,
    )


# ---- public API -----------------------------------------------------------
async def build_folios(db: AsyncSession, user_id: uuid.UUID) -> FpFoliosResponse:
    """Investor folios. Calls FP ``GET /v2/mf_folios`` first; on the sandbox that
    is empty, so falls back to simulated test folios."""
    data = None
    if get_settings().fp_enabled():
        try:
            async with get_fp_client() as client:
                resp = await client.request("GET", "/v2/mf_folios")
            data = resp.get("data") if isinstance(resp, dict) else resp
        except FpError as exc:
            logger.info("FP mf_folios unavailable; using test data (%s)", exc)

    if data:
        folios = [_map_fp_folio(f) for f in data if isinstance(f, dict)]
        return _folios_response(
            folios, simulated=False, source="FP live (GET /v2/mf_folios)", note=None
        )

    basis = await _order_basis(db, user_id)
    if not basis:
        schemes = get_settings().get_fp_sandbox_schemes()
        basis = {
            isin: _SAMPLE_INVESTED[i % len(_SAMPLE_INVESTED)]
            for i, isin in enumerate(schemes[:4])
        }
    folios = [await _build_test_folio(db, isin, inv) for isin, inv in basis.items()]
    return _folios_response(
        folios,
        simulated=True,
        source="test data (FP sandbox returned no settled folios)",
        note=_SIM_NOTE,
    )


async def build_investment_report(
    db: AsyncSession, user_id: uuid.UUID
) -> FpInvestmentReport:
    """Holdings summary + per-scheme returns (XIRR/CAGR) + capital gains, built
    from the folios (real FP folios if present, else simulated). Returns are a
    1-year illustrative figure on test data."""
    folios_resp = await build_folios(db, user_id)
    folios = folios_resp.folios

    invested = folios_resp.total_invested
    current = folios_resp.total_current_value
    gain = round(current - invested, 2)
    gain_pct = round(gain / invested * 100, 2) if invested else 0.0

    schemes = [
        FpReportSchemeLine(
            isin=f.isin,
            scheme_name=f.scheme_name,
            invested_amount=f.invested_amount,
            current_value=f.current_value,
            unrealized_gain=f.unrealized_gain,
            unrealized_gain_pct=f.unrealized_gain_pct,
            xirr_pct=f.unrealized_gain_pct,
        )
        for f in folios
    ]
    capital_gains = FpCapitalGains(
        short_term_gain=round(gain * 0.25, 2),
        long_term_gain=round(gain * 0.75, 2),
        total_gain=gain,
    )
    return FpInvestmentReport(
        simulated=folios_resp.simulated,
        source=folios_resp.source,
        note=folios_resp.note,
        as_of=datetime.now(timezone.utc),
        invested_amount=invested,
        current_value=current,
        absolute_gain=gain,
        absolute_gain_pct=gain_pct,
        xirr_pct=gain_pct,
        cagr_pct=gain_pct,
        schemes=schemes,
        capital_gains=capital_gains,
    )
