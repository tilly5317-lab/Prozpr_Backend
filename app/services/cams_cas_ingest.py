"""Application service — `cams_cas_ingest.py`.

Parse an uploaded CAMS / KFintech *Consolidated Account Statement* (CAS) PDF and
land it in the canonical ingestion tables:

1. Raw audit rows → ``mf_aa_imports`` + ``mf_aa_summaries`` + ``mf_aa_transactions``
   (same tables the Account-Aggregator feed used; the CAS shape maps cleanly onto them).
2. Normalised rows → ``mf_transactions`` (via :func:`app.services.mf_aa_normalizer.normalize_single_import`).
3. A bucketed roll-up of the statement valuations → primary-portfolio
   ``portfolio_allocations`` (Cash / Debt / Equity / Other), mirroring the SimBanks / Finvu
   shape so chat, drift, and allocation modules keep reading one canonical portfolio.

This replaces the (now sidelined) Finvu account-aggregator *fetch-by-mobile* flow,
which is paused for licensing reasons — see ``app/services/finvu_portfolio_sync.py``.

Heavy / optional dependency: ``casparser`` (imported lazily so the rest of the app boots
even when it is not installed).
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.mf import MfAaImport, MfAaImportStatus, MfAaSummary, MfAaTransaction
from app.models.portfolio import PortfolioAllocation, PortfolioHolding
from app.services.mf_aa_normalizer import normalize_single_import
from app.services.portfolio_service import get_or_create_primary_portfolio

logger = logging.getLogger(__name__)


# casparser transaction "type" -> the short flag that
# ``mf_aa_normalizer._map_transaction_type`` understands. Types not listed here
# (DIVIDEND_PAYOUT, STAMP_DUTY_TAX, STT_TAX, TDS_TAX, SEGREGATION, MISC, REVERSAL, UNKNOWN)
# do not move units / are not real holdings flows, so they are skipped.
_TXN_TYPE_FLAG: dict[str, str] = {
    "PURCHASE": "P",
    "PURCHASE_SIP": "P",
    "REDEMPTION": "R",
    "SWITCH_IN": "SI",
    "SWITCH_IN_MERGER": "SI",
    "SWITCH_OUT": "SO",
    "SWITCH_OUT_MERGER": "SO",
    "DIVIDEND_REINVEST": "DR",
    "DIVIDEND_REINVESTMENT": "DR",
}


class CamsPdfParseError(Exception):
    """The uploaded file is not a parseable CAS (wrong password, not a CAS PDF, no folios, …)."""

    def __init__(self, message: str, *, bad_password: bool = False) -> None:
        super().__init__(message)
        self.bad_password = bad_password


@dataclass(frozen=True)
class CamsIngestResult:
    import_id: uuid.UUID
    status: str
    cas_file_type: Optional[str]
    cas_type: Optional[str]
    statement_period_from: Optional[str]
    statement_period_to: Optional[str]
    folios: int
    schemes: int
    aa_transactions_parsed: int
    mf_transactions_inserted: int
    mf_transactions_skipped_duplicate: int
    portfolio_allocation_rows: int
    total_value_inr: float
    normalize_error: Optional[str] = None


# --------------------------------------------------------------------------- helpers


def _clean(value: object, *, limit: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _num(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _split_name(name: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    text = _clean(name)
    if not text:
        return None, None, None
    parts = text.split()
    if len(parts) == 1:
        return parts[0][:100], None, None
    if len(parts) == 2:
        return parts[0][:100], None, parts[1][:100]
    return parts[0][:100], " ".join(parts[1:-1])[:100], parts[-1][:100]


def _bucket_for_scheme_type(scheme_type: Optional[str]) -> str:
    """Map a CAS scheme classification onto the Cash / Debt / Equity / Other buckets."""
    t = (scheme_type or "").upper()
    if "EQUITY" in t:
        return "Equity"
    if any(k in t for k in ("LIQUID", "MONEY", "OVERNIGHT", "CASH")):
        return "Cash"
    if any(k in t for k in ("DEBT", "BOND", "GILT", "INCOME", "DURATION")):
        return "Debt"
    # HYBRID, BALANCED, FOF, SOLUTION, COMMODITY, OTHER, UNKNOWN, …
    return "Other"


_BAD_PASSWORD_MESSAGE = (
    "Couldn't open the PDF with that password. CAMS / KFintech Consolidated Account "
    "Statements are usually protected with the investor's PAN in CAPITAL letters (or the "
    "password you chose when requesting the statement)."
)
_NOT_MF_CAS_MESSAGE = (
    "This looks like an NSDL / CDSL demat e-CAS, which isn't supported here. Please upload "
    "the CAMS or KFintech mutual-fund Consolidated Account Statement instead."
)


def _to_plain_dict(result: object) -> dict[str, Any]:
    """`casparser.read_cas_pdf(output="dict")` returns a pydantic ``CASData`` model
    (>= 0.8) or a plain dict (older). Normalize to a JSON-safe dict either way."""
    if isinstance(result, dict):
        return result
    model_dump = getattr(result, "model_dump", None)  # pydantic v2
    if callable(model_dump):
        return model_dump(by_alias=True, mode="json")
    legacy_dict = getattr(result, "dict", None)  # pydantic v1
    if callable(legacy_dict):
        return legacy_dict(by_alias=True)
    raise CamsPdfParseError("Unexpected response from the CAS parser.")


def _parse_cas_pdf(data: bytes, password: str) -> dict[str, Any]:
    """Run casparser on the uploaded bytes. Synchronous / CPU-bound — call via a thread."""
    try:
        import casparser  # noqa: PLC0415  (heavy, optional — import lazily)
        from casparser.exceptions import (  # noqa: PLC0415
            CASParseError,
            IncorrectPasswordError,
            ParserException,
        )
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise CamsPdfParseError(
            "CAMS PDF parsing is unavailable on this server "
            "(the 'casparser' package is not installed or is too old)."
        ) from exc

    def _looks_like_password_error(text: str) -> bool:
        t = text.lower()
        return any(k in t for k in ("password", "decrypt", "encrypt", "crypt"))

    try:
        result = casparser.read_cas_pdf(io.BytesIO(data), password, output="dict")
    except IncorrectPasswordError as exc:
        raise CamsPdfParseError(_BAD_PASSWORD_MESSAGE, bad_password=True) from exc
    except (CASParseError, ParserException) as exc:
        text = str(exc)
        if _looks_like_password_error(text):
            raise CamsPdfParseError(_BAD_PASSWORD_MESSAGE, bad_password=True) from exc
        if "pdfminer does not support" in text.lower() or "pymupdf" in text.lower():
            raise CamsPdfParseError(_NOT_MF_CAS_MESSAGE) from exc
        raise CamsPdfParseError(
            f"Couldn't read this file as a CAMS / KFintech statement: {text}"
        ) from exc
    except Exception as exc:  # last resort — e.g. a low-level pdf decryption error
        text = str(exc)
        bad_pw = _looks_like_password_error(text)
        raise CamsPdfParseError(
            _BAD_PASSWORD_MESSAGE
            if bad_pw
            else f"Couldn't read this file as a CAMS / KFintech statement: {text}",
            bad_password=bad_pw,
        ) from exc

    parsed = _to_plain_dict(result)
    if not isinstance(parsed, dict):
        raise CamsPdfParseError("Unexpected response from the CAS parser.")
    if not parsed.get("folios"):
        # NSDL / CDSL e-CAS comes back with "accounts" instead of "folios".
        if parsed.get("accounts"):
            raise CamsPdfParseError(_NOT_MF_CAS_MESSAGE)
        raise CamsPdfParseError("No mutual-fund folios were found in this statement.")
    return parsed


def _build_import_row(
    user_id: uuid.UUID, parsed: dict[str, Any], source_filename: Optional[str]
) -> MfAaImport:
    period = parsed.get("statement_period") or {}
    investor = parsed.get("investor_info") or {}
    folios = parsed.get("folios") or []

    pans = {p for p in (_clean(f.get("PAN")) for f in folios) if p}
    first, middle, last = _split_name(investor.get("name"))

    return MfAaImport(
        user_id=user_id,
        pan=_clean(next(iter(pans), None), limit=20),
        email=_clean(investor.get("email"), limit=320),
        mobile=_clean(investor.get("mobile"), limit=20),
        from_date=_clean(period.get("from"), limit=20),
        to_date=_clean(period.get("to"), limit=20),
        # synthetic — satisfies uq_mf_aa_import_req_email for uploaded (non-AA-feed) imports
        req_id=uuid.uuid4().hex,
        investor_first_name=first,
        investor_middle_name=middle,
        investor_last_name=last,
        address_line_1=_clean(investor.get("address"), limit=255),
        source_file=_clean(source_filename, limit=255) or "cams_cas.pdf",
        status=MfAaImportStatus.RECEIVED,
        failure_reason=None,
    )


def _populate_children(aa_import: MfAaImport, parsed: dict[str, Any]) -> tuple[int, int, dict[str, float], float]:
    """Append MfAaSummary + MfAaTransaction children; return (schemes, txns, bucket_values, cost_total)."""
    folios = parsed.get("folios") or []
    scheme_count = 0
    txn_count = 0
    bucket_value: dict[str, float] = {"Cash": 0.0, "Debt": 0.0, "Equity": 0.0, "Other": 0.0}
    cost_total = 0.0

    for folio in folios:
        folio_no = _clean(folio.get("folio"), limit=40)
        amc_name = _clean(folio.get("amc"), limit=200)
        for scheme in folio.get("schemes") or []:
            scheme_count += 1
            stype = _clean(scheme.get("type"))
            amfi = _clean(scheme.get("amfi"), limit=20)
            isin = _clean(scheme.get("isin"), limit=20)
            scheme_code = amfi or isin
            scheme_name = _clean(scheme.get("scheme"), limit=255)
            valuation = scheme.get("valuation") or {}
            market_value = _num(valuation.get("value"))
            scheme_cost = _num(valuation.get("cost"))
            if scheme_cost > 0:
                cost_total += scheme_cost
            if market_value > 0:
                bucket_value[_bucket_for_scheme_type(stype)] += market_value

            txns = scheme.get("transactions") or []
            last_txn_date = _clean(txns[-1].get("date")) if txns else None

            aa_import.summaries.append(
                MfAaSummary(
                    row_no=scheme_count,
                    amc_name=amc_name,
                    asset_type=_clean(stype, limit=30),
                    folio=folio_no,
                    isin=isin,
                    scheme=_clean(scheme_code, limit=20),
                    scheme_name=scheme_name,
                    closing_balance=_num(scheme.get("close")),
                    cost_value=(scheme_cost or None),
                    market_value=(market_value or None),
                    nav=(_num(valuation.get("nav")) or None),
                    last_nav_date=_clean(valuation.get("date"), limit=20),
                    last_trxn_date=last_txn_date,
                    rta_code=_clean(scheme.get("rta_code"), limit=30),
                )
            )

            for txn in txns:
                flag = _TXN_TYPE_FLAG.get(str(txn.get("type") or "").upper())
                if flag is None:
                    continue
                txn_count += 1
                aa_import.transactions.append(
                    MfAaTransaction(
                        row_no=txn_count,
                        amc_name=amc_name,
                        folio=folio_no,
                        isin=isin,
                        scheme=_clean(scheme_code, limit=20),
                        scheme_name=scheme_name,
                        posted_date=_clean(txn.get("date"), limit=20),
                        trxn_date=_clean(txn.get("date"), limit=20),
                        trxn_amount=_num(txn.get("amount")),
                        trxn_units=_num(txn.get("units")),
                        purchase_price=(_num(txn.get("nav")) or None),
                        trxn_desc=_clean(txn.get("description"), limit=100),
                        trxn_type_flag=flag,
                    )
                )

    return scheme_count, txn_count, bucket_value, cost_total


async def _apply_portfolio_rollup(
    db: AsyncSession, user_id: uuid.UUID, bucket_value: dict[str, float], cost_total: float
) -> tuple[int, float]:
    """Replace the primary portfolio's bucket allocations with the CAS valuation roll-up."""
    total = sum(v for v in bucket_value.values() if v > 0)
    if total <= 0:
        return 0, 0.0

    portfolio = await get_or_create_primary_portfolio(db, user_id)
    await db.execute(
        delete(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio.id)
    )
    rows = 0
    for name, raw in bucket_value.items():
        if raw <= 0:
            continue
        db.add(
            PortfolioAllocation(
                portfolio_id=portfolio.id,
                asset_class=name,
                allocation_percentage=round(100.0 * raw / total, 2),
                amount=round(raw, 2),
            )
        )
        rows += 1
    portfolio.total_value = round(total, 2)
    portfolio.total_invested = round(cost_total if cost_total > 0 else total, 2)
    await db.flush()
    return rows, total


async def _sync_mf_portfolio_holdings_from_cas(
    db: AsyncSession,
    portfolio_id: uuid.UUID,
    parsed: dict[str, Any],
    portfolio_total: float,
) -> int:
    """Replace MF rows in ``portfolio_holdings`` with one line per CAS scheme so the app can list each fund/ETF."""
    await db.execute(
        delete(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.instrument_type == "mutual_fund",
        )
    )
    if portfolio_total <= 0:
        await db.flush()
        return 0

    written = 0
    for folio in parsed.get("folios") or []:
        folio_no = _clean(folio.get("folio"), limit=40)
        for scheme in folio.get("schemes") or []:
            scheme_name = _clean(scheme.get("scheme"), limit=255) or "Mutual fund scheme"
            valuation = scheme.get("valuation") or {}
            market_value = _num(valuation.get("value"))
            if market_value <= 0:
                continue

            scheme_cost = _num(valuation.get("cost"))
            nav = _num(valuation.get("nav"))
            units = _num(scheme.get("close"))
            amfi = _clean(scheme.get("amfi"), limit=20)
            isin = _clean(scheme.get("isin"), limit=20)
            ticker_raw = amfi or isin
            ticker = ticker_raw[:20] if ticker_raw else None

            avg_cost: Optional[float] = None
            if units > 0 and scheme_cost > 0:
                avg_cost = round(scheme_cost / units, 6)

            current_price: Optional[float] = None
            if nav > 0:
                current_price = round(nav, 6)
            elif units > 0:
                current_price = round(market_value / units, 6)

            pct = round(100.0 * market_value / portfolio_total, 4)

            folio_bit = f" · Folio {folio_no}" if folio_no else ""
            max_name = 255 - len(folio_bit)
            base_name = scheme_name if len(scheme_name) <= max_name else scheme_name[: max(0, max_name)]
            display_name = f"{base_name}{folio_bit}" if folio_bit else scheme_name[:255]

            db.add(
                PortfolioHolding(
                    portfolio_id=portfolio_id,
                    instrument_name=display_name,
                    instrument_type="mutual_fund",
                    ticker_symbol=ticker,
                    quantity=round(units, 4) if units > 0 else None,
                    average_cost=avg_cost,
                    current_price=current_price,
                    current_value=round(market_value, 2),
                    allocation_percentage=pct,
                )
            )
            written += 1

    await db.flush()
    return written


# --------------------------------------------------------------------------- entry point


async def ingest_cams_pdf(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    file_bytes: bytes,
    password: str,
    source_filename: Optional[str] = None,
) -> CamsIngestResult:
    """Parse a CAMS/KFintech CAS PDF and persist it. Caller is responsible for the final commit."""
    parsed = await asyncio.to_thread(_parse_cas_pdf, file_bytes, password)

    aa_import = _build_import_row(user_id, parsed, source_filename)
    db.add(aa_import)
    scheme_count, txn_count, bucket_value, cost_total = _populate_children(aa_import, parsed)
    # Durably store the raw import + children first, so a normalization failure below
    # still leaves a retry-able RECEIVED row (see /mf-ingest/normalize/{import_id}).
    await db.commit()

    aa_import = (
        await db.execute(
            select(MfAaImport)
            .where(MfAaImport.id == aa_import.id)
            .options(
                selectinload(MfAaImport.summaries),
                selectinload(MfAaImport.transactions),
            )
        )
    ).scalar_one()
    # Capture scalars before normalize_single_import's internal commit expires the instance.
    import_id = aa_import.id
    stmt_from = aa_import.from_date
    stmt_to = aa_import.to_date

    norm = await normalize_single_import(db, aa_import)

    alloc_rows, total_value = await _apply_portfolio_rollup(db, user_id, bucket_value, cost_total)
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    await _sync_mf_portfolio_holdings_from_cas(db, portfolio.id, parsed, total_value)

    return CamsIngestResult(
        import_id=import_id,
        status=norm.status.value,
        cas_file_type=_clean(parsed.get("file_type")),
        cas_type=_clean(parsed.get("cas_type")),
        statement_period_from=stmt_from,
        statement_period_to=stmt_to,
        folios=len(parsed.get("folios") or []),
        schemes=scheme_count,
        aa_transactions_parsed=txn_count,
        mf_transactions_inserted=norm.inserted,
        mf_transactions_skipped_duplicate=norm.skipped_duplicate,
        portfolio_allocation_rows=alloc_rows,
        total_value_inr=round(total_value, 2),
        normalize_error=norm.error,
    )
