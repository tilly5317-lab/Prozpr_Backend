"""Application service — `cams_cas_ingest.py`.

Parse an uploaded CAMS / KFintech *Consolidated Account Statement* (CAS) PDF —
via the CAS Parser API (https://casparser.in, see ``casparser_client`` /
``casparser_adapter``) — and land it in the canonical ingestion tables:

1. Raw audit rows → ``mf_aa_imports`` + ``mf_aa_summaries`` + ``mf_aa_transactions``
   (same tables the Account-Aggregator feed used; the CAS shape maps cleanly onto them).
2. Normalised rows → ``mf_transactions`` (via :func:`app.domains.ingestion.services.mf_aa_normalizer.normalize_single_import`).
3. A bucketed roll-up → primary-portfolio ``portfolio_allocations`` (Cash / Debt /
   Equity / Other), mirroring the SimBanks / Finvu shape so chat, drift, and
   allocation modules keep reading one canonical portfolio. Bucket values and
   per-scheme ``portfolio_holdings`` rows are DERIVED FROM THE STATEMENT'S
   TRANSACTIONS (units summed per scheme × statement NAV), not the CAS valuation
   block, so they always equal the ``mf_transactions`` summary (`mf_holdings` view,
   fund-detail page). The raw ``mf_aa_summaries`` audit rows still mirror the
   statement's stated valuations verbatim.

This replaces the (now sidelined) Finvu account-aggregator *fetch-by-mobile* flow,
which is paused for licensing reasons — see ``app/services/finvu_portfolio_sync.py``.

Parsing is REMOTE: the PDF is posted to ``api.casparser.in`` (`/v4/smart/parse`)
and the unified JSON response is adapted back onto the legacy parsed-CAS shape
(``casparser_adapter.to_legacy_parsed``) so every downstream invariant —
transaction-derived holdings, the statement-balance guard, SUMMARY/zero-value
rejection — is untouched. The old in-process ``casparser``/``pymupdf`` parsing
(and its version-pinning woes) is gone.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.mutual_funds.models import (
    MfAaImport,
    MfAaImportStatus,
    MfAaSummary,
    MfAaTransaction,
    UserCasDocument,
)
from app.domains.mutual_funds.services.scheme_classification import (
    classify_holding,
    resolve_asset_bucket,
)
from app.domains.mutual_funds.services.scheme_resolver import (
    build_isin_to_amfi_map,
    canonical_scheme_code,
)
from app.domains.portfolio.models.portfolio import (
    PortfolioAllocation,
    PortfolioHolding,
)
from app.core.config import Settings
from app.domains.identity.models.user import User
from app.domains.ingestion.services.casparser_adapter import (
    CasResponseShapeError,
    to_legacy_parsed,
)
from app.domains.ingestion.services.cams_pdf_stage import (
    archive_cas_pdf,
    discard_staged_pdf,
    stage_enabled,
    stage_pdf,
)
from app.domains.ingestion.services.casparser_client import (
    CasParserApiError,
    get_casparser_client,
)
from app.domains.ingestion.services.mf_aa_normalizer import normalize_single_import
from app.core.cas_scope import (
    get_scope,
    scope_filter,
    scoped_to,
    versioning_enabled,
)
from app.domains.cashflow.services.cashflow_persist_service import (
    mark_stale as mark_cashflow_stale,
)
from app.domains.ingestion.services import cas_upload_service
from app.domains.ingestion.services.user_data_reset import reset_user_financial_data
from app.domains.mutual_funds.services.txn_value import trade_value
from app.domains.portfolio.services.portfolio_service import (
    get_or_create_primary_portfolio,
)

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

# Flags that add units to the position; the rest (R / SO) remove them. Mirrors
# `_INFLOW_TYPES` in mutual_funds/services/holding_detail_service.py so the
# ingest-time position and the fund-detail page compute the same balance.
_INFLOW_FLAGS: frozenset[str] = frozenset({"P", "SI", "DR"})


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
    # The snapshot this upload created (None only when versioning is switched off).
    # Everything written by this ingest carries it, and the app reads only rows
    # that do.
    cas_upload_id: Optional[uuid.UUID] = None
    normalize_error: Optional[str] = None
    # True when the user re-uploaded the byte-identical PDF that is already
    # active: nothing was parsed or written, the live snapshot is returned as-is.
    reused_existing: bool = False
    # Identity fields on the `users` row that were back-filled from the CAS investor
    # block (only ever fills blanks — never overwrites what the user already set).
    profile_fields_filled: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _clean(value: object, *, limit: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


# CAMS / KFintech banners often include ``… - ISIN: INF1234567890 …`` even when
# casparser's bundled ISIN table misses newer schemes (NFOs, FoFs, Groww-only codes).
_ISIN_IN_BANNER_RE = re.compile(
    r"\bISIN\s*[: ]\s*([A-Z]{2}[A-Z0-9]{9}\d)\b",
    re.IGNORECASE,
)
# Indian mutual-fund growth ISINs almost always start with INF; CAMS sometimes embeds
# the code without an explicit ``ISIN:`` label in the string casparser keeps.
_MF_ISIN_LOOSE_RE = re.compile(r"\b(INF[A-Z0-9]{9}\d)\b", re.IGNORECASE)


def _isin_from_scheme_strings(*chunks: Optional[str]) -> Optional[str]:
    """Pull a 12-char ISIN from free text (scheme title line, RTA banner, etc.)."""
    blob = " ".join(c for c in chunks if c and str(c).strip())
    if not blob:
        return None
    m = _ISIN_IN_BANNER_RE.search(blob)
    if m:
        code = m.group(1).strip().upper()
        if len(code) == 12 and code.isalnum():
            return code[:20]
    m2 = _MF_ISIN_LOOSE_RE.search(blob)
    if m2:
        code = m2.group(1).strip().upper()
        if len(code) == 12 and code.isalnum():
            return code[:20]
    return None


def _resolve_scheme_identifiers(
    scheme: dict[str, Any], scheme_name: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(amfi, isin)`` with ISIN inferred from banner text when casparser omits it."""
    amfi = _clean(scheme.get("amfi"), limit=20)
    isin = _clean(scheme.get("isin"), limit=20)
    if not isin:
        parts: list[str] = []
        if scheme_name:
            parts.append(scheme_name)
        for v in scheme.values():
            if isinstance(v, str) and str(v).strip():
                parts.append(str(v).strip())
        isin = _isin_from_scheme_strings(" ".join(parts)) if parts else None
    return amfi, isin


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


@dataclass(frozen=True)
class _SchemeSnapshot:
    """Per-scheme position used for portfolio rows — derived from the statement's
    own transactions so holdings are exactly the roll-up of `mf_transactions`
    (matching the `mf_holdings` view and the fund-detail ledger), not the CAS
    valuation block, which routinely disagrees with the transaction sum."""

    units: float
    avg_cost: Optional[float]  # units-weighted purchase NAV (None when unknown)
    nav: float  # statement NAV (0 when the CAS omits it)
    market_value: float
    invested: float  # cost basis of the units still held
    derived_from_txns: bool


def _derive_scheme_snapshot(scheme: dict[str, Any]) -> _SchemeSnapshot:
    """Compute a scheme's position by summing its unit-moving transactions.

    Uses the same allow-list (`_TXN_TYPE_FLAG`) and abs-units + type-direction
    convention as the `mf_transactions` ledger, so the numbers pushed into
    `portfolio_holdings` / allocations equal the transaction summary. Falls back
    to the CAS-stated close balance & valuation when the parser found no
    unit-moving transactions for the scheme, OR when the transaction-derived
    units contradict the statement's own opening→closing balance — incomplete
    extraction (environment-dependent casparser/pdf quirks) otherwise inflates
    or deflates a holding's units and corrupts the portfolio value.
    """
    valuation = scheme.get("valuation") or {}
    stated_value = _num(valuation.get("value"))
    stated_cost = _num(valuation.get("cost"))
    stated_units = _num(scheme.get("close") or scheme.get("close_calculated"))
    nav = _num(valuation.get("nav"))
    if nav <= 0 and stated_units > 0 and stated_value > 0:
        nav = stated_value / stated_units

    # A statement over a partial period starts each scheme at a non-zero opening
    # balance; the transaction sum only covers the period, so the position is
    # opening + Σ(signed txn units).
    opening_units = _num(scheme.get("open"))
    units = opening_units
    buy_units = 0.0
    buy_cost = 0.0
    seen = False
    for txn in scheme.get("transactions") or []:
        flag = _TXN_TYPE_FLAG.get(str(txn.get("type") or "").upper())
        if flag is None:
            continue
        seen = True
        # CAS prints redemption units/amounts as negatives — take the magnitude
        # and let the transaction type decide direction.
        u = abs(_num(txn.get("units")))
        # Not abs(amount) — a statement row whose amount column was mis-parsed is
        # priced off units x NAV, the same way every reader of the normalized
        # ledger prices it (``mutual_funds.services.txn_value``).
        amount = trade_value(
            _num(txn.get("units")), _num(txn.get("nav")), _num(txn.get("amount"))
        )
        if flag in _INFLOW_FLAGS:
            units += u
            buy_units += u
            buy_cost += amount
        else:
            units -= u

    def _statement_position() -> _SchemeSnapshot:
        avg = (
            (stated_cost / stated_units)
            if (stated_units > 0 and stated_cost > 0)
            else None
        )
        market_value = (
            stated_value
            if stated_value > 0
            else (stated_units * nav if (stated_units > 0 and nav > 0) else 0.0)
        )
        return _SchemeSnapshot(
            units=stated_units,
            avg_cost=avg,
            nav=nav,
            market_value=market_value,
            invested=stated_cost if stated_cost > 0 else market_value,
            derived_from_txns=False,
        )

    if not seen:
        return _statement_position()

    # Sanity gate: the extracted transactions must reproduce the statement's own
    # closing balance (opening + Σtxns == close). When they don't, the txn table
    # was extracted incompletely / misparsed for this scheme (this varies with
    # the upstream parser for this scheme), and pricing those units would
    # inflate or deflate the holding — trust the statement's stated position.
    if stated_units > 0 and abs(units - stated_units) > max(0.5, stated_units * 0.01):
        logger.warning(
            "CAS ingest: txn-derived units %.4f contradict statement close balance "
            "%.4f for scheme %r — txn extraction incomplete; using the statement "
            "position",
            units,
            stated_units,
            _clean(scheme.get("scheme"), limit=60),
        )
        return _statement_position()

    if units <= 1e-6:  # fully redeemed per the ledger — no live position
        return _SchemeSnapshot(
            units=0.0,
            avg_cost=None,
            nav=nav,
            market_value=0.0,
            invested=0.0,
            derived_from_txns=True,
        )

    avg_cost = (buy_cost / buy_units) if buy_units > 0 else None
    market_value = units * nav if nav > 0 else stated_value
    invested = (avg_cost * units) if avg_cost is not None else 0.0
    return _SchemeSnapshot(
        units=units,
        avg_cost=avg_cost,
        nav=nav,
        market_value=market_value,
        invested=invested,
        derived_from_txns=True,
    )


def _split_name(
    name: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    text = _clean(name)
    if not text:
        return None, None, None
    parts = text.split()
    if len(parts) == 1:
        return parts[0][:100], None, None
    if len(parts) == 2:
        return parts[0][:100], None, parts[1][:100]
    return parts[0][:100], " ".join(parts[1:-1])[:100], parts[-1][:100]


# --------------------------------------------------------------------------- scheme classification
#
# casparser only tags a scheme as ``EQUITY`` / ``DEBT`` (or ``N/A`` / ``UNKNOWN``)
# and it does so purely from a bundled MFCentral ISIN list. Newer schemes, ETFs,
# index/international funds and NFOs that aren't in that list come back as ``N/A``.
# Resolution flows through the canonical classifier in
# ``app/domains/mutual_funds/services/scheme_classification.py``:
#   1. ``classify_holding(None, scheme_name)`` — fires the name-based
#      ``arbitrage_plus_income`` override when applicable.
#   2. ``resolve_asset_bucket(scheme_type, scheme_name)`` — trusts casparser's
#      ``type`` when it actually classified the scheme; otherwise name-based.
# Output vocabulary: ``Equity`` / ``Debt`` / ``Others`` (3 canonical buckets).

# casparser ``type`` values that mean "I couldn't classify it" — used below to
# decide whether to persist the upstream type verbatim or replace it with the
# resolved bucket. Local to this file (no classification concern).
_CASPARSER_UNKNOWN_TYPES: frozenset[str] = frozenset(
    {"", "N/A", "NA", "UNKNOWN", "OTHER", "NONE"}
)


def _resolve_cas_bucket(scheme_type: Optional[str], scheme_name: Optional[str]) -> str:
    """Resolve a CAS scheme into the canonical 3-bucket asset_class."""
    ac, _ = classify_holding(None, scheme_name)
    if ac is not None:
        return ac
    return resolve_asset_bucket(scheme_type, scheme_name)


_BAD_PASSWORD_MESSAGE = (
    "Couldn't open the PDF with that password. CAMS / KFintech Consolidated Account "
    "Statements are usually protected with the investor's PAN in CAPITAL letters (or the "
    "password you chose when requesting the statement)."
)
_NOT_MF_CAS_MESSAGE = (
    "This looks like an NSDL / CDSL demat e-CAS, which isn't supported here. Please upload "
    "the CAMS or KFintech mutual-fund Consolidated Account Statement instead."
)


# --------------------------------------------------------------------------- remote parse


def _looks_like_password_error(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("password", "decrypt", "encrypt", "crypt"))


async def _parse_cas_via_api(
    file_bytes: bytes, source_filename: Optional[str], password: str
) -> dict[str, Any]:
    """Parse the uploaded CAS PDF remotely via the CAS Parser API and adapt the
    response onto the legacy parsed-CAS dict. Every user-facing failure raises
    :class:`CamsPdfParseError` (``bad_password=True`` → HTTP 400, else 422)."""
    if not Settings.casparser_enabled():
        raise CamsPdfParseError(
            "CAS statement parsing is unavailable on this server "
            "(CASPARSER_API_KEY is not configured)."
        )
    client = get_casparser_client()
    # casparser's edge caps multipart bodies (~1.8 MB plan-dependent), but the
    # pdf_url mode has no size cap: stage big files in the private S3 bucket
    # and hand casparser a short-lived presigned URL. Falls back to multipart
    # when no bucket is configured (e.g. local dev).
    use_url_mode = (
        len(file_bytes) > Settings.get_casparser_multipart_max_bytes()
        and stage_enabled()
    )
    try:
        if use_url_mode:
            pdf_url, staged_key = await stage_pdf(file_bytes)
            try:
                payload = await client.smart_parse_url(pdf_url, password)
            finally:
                await discard_staged_pdf(staged_key)
        else:
            payload = await client.smart_parse(
                file_bytes, source_filename or "cams_cas.pdf", password
            )
    except CasParserApiError as exc:
        reason = exc.short_reason
        if exc.status_code in (401, 402):
            # Our key is bad or the account ran out of credits — an ops problem,
            # never the user's fault. Log loudly; ask the user to retry later.
            logger.error("CAS Parser API auth/credit failure: %s", reason)
            raise CamsPdfParseError(
                "The statement-parsing service is temporarily unavailable. "
                "Please try again in a little while."
            ) from exc
        if exc.status_code == 400:
            if _looks_like_password_error(reason):
                raise CamsPdfParseError(
                    _BAD_PASSWORD_MESSAGE, bad_password=True
                ) from exc
            raise CamsPdfParseError(
                f"Couldn't read this file as a CAMS / KFintech statement: {reason}"
            ) from exc
        if exc.status_code == 413:
            # casparser.in's edge rejects request bodies over ~2 MB (verified
            # empirically 2026-08-04) — far below our own 20 MB gate. Retrying
            # the same file can never succeed, so say what's actually wrong.
            logger.warning(
                "CAS Parser API rejected a %d KB PDF as too large",
                len(file_bytes) // 1024,
            )
            raise CamsPdfParseError(
                "This statement PDF is larger than our parsing service currently "
                "accepts (about 2 MB). Please request a CAS covering a shorter "
                "period and upload that instead."
            ) from exc
        raise CamsPdfParseError(
            "The statement-parsing service could not be reached. "
            "Please try again in a minute."
        ) from exc

    # Domain failures arrive INSIDE a 200 body: {"status": "failed", "msg": ...}
    if str(payload.get("status") or "").strip().lower() == "failed":
        reason = str(payload.get("msg") or "unknown parse failure")
        if _looks_like_password_error(reason):
            raise CamsPdfParseError(_BAD_PASSWORD_MESSAGE, bad_password=True)
        raise CamsPdfParseError(
            f"Couldn't read this file as a CAMS / KFintech statement: {reason}"
        )

    try:
        return to_legacy_parsed(payload)
    except CasResponseShapeError as exc:
        raise CamsPdfParseError(
            _NOT_MF_CAS_MESSAGE if exc.demat_statement else str(exc)
        ) from exc


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
        cas_type=_clean(parsed.get("cas_type"), limit=20),
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


def _populate_children(
    aa_import: MfAaImport, parsed: dict[str, Any]
) -> tuple[int, int, dict[str, float], float]:
    """Append MfAaSummary + MfAaTransaction children; return (schemes, txns, bucket_values, cost_total)."""
    folios = parsed.get("folios") or []
    scheme_count = 0
    txn_count = 0
    bucket_value: dict[str, float] = {"Equity": 0.0, "Debt": 0.0, "Others": 0.0}
    cost_total = 0.0

    for folio in folios:
        folio_no = _clean(folio.get("folio"), limit=40)
        amc_name = _clean(folio.get("amc"), limit=200)
        for scheme in folio.get("schemes") or []:
            scheme_count += 1
            stype = _clean(scheme.get("type"))
            scheme_name = _clean(scheme.get("scheme"), limit=255)
            amfi, isin = _resolve_scheme_identifiers(scheme, scheme_name)
            scheme_code = amfi or isin
            valuation = scheme.get("valuation") or {}
            # Audit rows (MfAaSummary below) keep the CAS-stated numbers verbatim;
            # the portfolio roll-up uses the transaction-derived position so the
            # bucket totals equal the sum of `mf_transactions`.
            market_value = _num(valuation.get("value"))
            scheme_cost = _num(valuation.get("cost"))
            snapshot = _derive_scheme_snapshot(scheme)
            if snapshot.invested > 0:
                cost_total += snapshot.invested

            bucket = _resolve_cas_bucket(stype, scheme_name)
            if snapshot.market_value > 0:
                bucket_value[bucket] += snapshot.market_value
            # Persist the resolved class when casparser couldn't classify it, so
            # `mf_fund_metadata.category` (derived from this) isn't left as "N/A".
            resolved_asset_type = (
                stype
                if stype and stype.strip().upper() not in _CASPARSER_UNKNOWN_TYPES
                else bucket.upper()
            )

            txns = scheme.get("transactions") or []
            last_txn_date = _clean(txns[-1].get("date")) if txns else None

            aa_import.summaries.append(
                MfAaSummary(
                    row_no=scheme_count,
                    amc_name=amc_name,
                    asset_type=_clean(resolved_asset_type, limit=30),
                    folio=folio_no,
                    isin=isin,
                    scheme=_clean(scheme_code, limit=20),
                    scheme_name=scheme_name,
                    closing_balance=_num(
                        scheme.get("close") or scheme.get("close_calculated")
                    ),
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
                        stamp_duty=(_num(txn.get("stamp_duty")) or None),
                        stt_tax=(_num(txn.get("stt")) or None),
                    )
                )

    return scheme_count, txn_count, bucket_value, cost_total


async def _apply_portfolio_rollup(
    db: AsyncSession,
    user_id: uuid.UUID,
    bucket_value: dict[str, float],
    cost_total: float,
) -> tuple[int, float]:
    """Replace the primary portfolio's bucket allocations with the transaction-derived roll-up."""
    total = sum(v for v in bucket_value.values() if v > 0)
    if total <= 0:
        return 0, 0.0

    portfolio = await get_or_create_primary_portfolio(db, user_id)
    # Only this snapshot's allocation rows are cleared — the previous statement's
    # roll-up stays on disk, superseded but intact.
    await db.execute(
        delete(PortfolioAllocation).where(
            PortfolioAllocation.portfolio_id == portfolio.id,
            *scope_filter(PortfolioAllocation, get_scope()),
        )
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
    """Replace MF rows in ``portfolio_holdings`` with one line per FUND so the app can list each fund/ETF.

    A fund held in several folios (bought through different platforms/brokers)
    appears in the CAS once per folio; those per-folio positions are merged into
    a single row keyed on the fund's canonical identity (AMFI code → ISIN →
    scheme name), so the app never lists the same fund twice.
    """
    await db.execute(
        delete(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.instrument_type == "mutual_fund",
            *scope_filter(PortfolioHolding, get_scope()),
        )
    )
    if portfolio_total <= 0:
        await db.flush()
        return 0

    # Resolve ISIN-keyed schemes to canonical AMFI codes so a holding's ticker_symbol
    # matches mf_nav_history / mfapi.in and can be priced & refreshed. Built once for the
    # whole statement from every (amfi, isin) pair we can see.
    candidate_isins: set[str] = set()
    for folio in parsed.get("folios") or []:
        for scheme in folio.get("schemes") or []:
            amfi, isin = _resolve_scheme_identifiers(
                scheme, _clean(scheme.get("scheme"))
            )
            for val in (amfi, isin):
                if val and not val.isdigit():
                    candidate_isins.add(val.upper())
    isin_to_amfi = await build_isin_to_amfi_map(db, candidate_isins)

    # Merge per-folio positions of the SAME fund (multi-platform purchases) into
    # one group per canonical fund identity before writing rows.
    groups: dict[str, dict[str, Any]] = {}
    for folio in parsed.get("folios") or []:
        folio_no = _clean(folio.get("folio"), limit=40)
        for scheme in folio.get("schemes") or []:
            scheme_name = (
                _clean(scheme.get("scheme"), limit=255) or "Mutual fund scheme"
            )
            # Position = roll-up of the statement's transactions (units, cost,
            # value), so this row always agrees with `mf_transactions` and the
            # `mf_holdings` view — not the CAS valuation block.
            snapshot = _derive_scheme_snapshot(scheme)
            if snapshot.market_value <= 0:
                continue

            amfi, isin = _resolve_scheme_identifiers(scheme, scheme_name)
            ticker_raw = canonical_scheme_code(
                amfi=amfi, isin=isin, isin_to_amfi=isin_to_amfi
            )
            ticker = ticker_raw[:20] if ticker_raw else None
            key = (
                ticker
                or (isin.upper() if isin else None)
                or f"name:{scheme_name.casefold()}"
            )

            group = groups.get(key)
            if group is None:
                group = {
                    "scheme_name": scheme_name,
                    "ticker": ticker,
                    "units": 0.0,
                    "market_value": 0.0,
                    "buy_cost": 0.0,  # Σ(avg_cost × units) over folios with a known cost
                    "costed_units": 0.0,
                    "nav": 0.0,
                    "folios": [],
                }
                groups[key] = group
            group["units"] += snapshot.units
            group["market_value"] += snapshot.market_value
            if snapshot.avg_cost is not None and snapshot.units > 0:
                group["buy_cost"] += snapshot.avg_cost * snapshot.units
                group["costed_units"] += snapshot.units
            if group["nav"] <= 0 and snapshot.nav > 0:
                group["nav"] = snapshot.nav
            if folio_no and folio_no not in group["folios"]:
                group["folios"].append(folio_no)

    written = 0
    for group in groups.values():
        market_value = group["market_value"]
        units = group["units"]
        scheme_name = group["scheme_name"]

        # Units-weighted purchase NAV across all folios of the fund.
        avg_cost: Optional[float] = (
            round(group["buy_cost"] / group["costed_units"], 6)
            if group["costed_units"] > 0
            else None
        )

        current_price: Optional[float] = None
        if group["nav"] > 0:
            current_price = round(group["nav"], 6)
        elif units > 0:
            current_price = round(market_value / units, 6)

        pct = round(100.0 * market_value / portfolio_total, 4)

        folios = group["folios"]
        if not folios:
            folio_bit = ""
        elif len(folios) == 1:
            folio_bit = f" · Folio {folios[0]}"
        else:
            joined = ", ".join(folios)
            # Keep the "Folio…" prefix even when abbreviating — the frontend
            # strips the suffix with /·\s*Folio.*$/i.
            folio_bit = (
                f" · Folios {joined}"
                if len(joined) <= 80
                else f" · Folio {folios[0]} & {len(folios) - 1} more"
            )
        max_name = 255 - len(folio_bit)
        base_name = (
            scheme_name
            if len(scheme_name) <= max_name
            else scheme_name[: max(0, max_name)]
        )
        display_name = f"{base_name}{folio_bit}" if folio_bit else scheme_name[:255]

        db.add(
            PortfolioHolding(
                portfolio_id=portfolio_id,
                instrument_name=display_name,
                instrument_type="mutual_fund",
                ticker_symbol=group["ticker"],
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


async def _backfill_user_profile(
    db: AsyncSession, user_id: uuid.UUID, parsed: dict[str, Any]
) -> list[str]:
    """Fill empty identity fields on the user's row from the CAS investor block.

    The CAS carries the investor's email, PAN and (a) postal address — when the
    user signed up with only a phone number these are blank, so ``/profile`` and
    ``/auth/me`` come back empty. We back-fill **blanks only**: anything the user
    has already entered is left untouched. Returns the list of fields filled.

    The investor NAME is deliberately NOT taken from the CAS — the name the user
    enters at sign-up is authoritative. (The CAS legal name is still recorded in
    the ``mf_aa_imports`` audit row; it just never lands on the user profile.)
    """
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        return []

    investor = parsed.get("investor_info") or {}
    folios = parsed.get("folios") or []
    filled: list[str] = []

    # NOTE: the investor name is intentionally NOT back-filled from the CAS.

    address = _clean(investor.get("address"), limit=500)
    if address and not _clean(user.address):
        user.address = address
        filled.append("address")

    email = _clean(investor.get("email"), limit=320)
    if email and "@" in email and not _clean(user.email):
        # users.email is unique — only claim it if no other user holds it.
        clash = (
            await db.execute(
                select(User.id).where(User.email == email, User.id != user_id)
            )
        ).first()
        if clash is None:
            user.email = email
            filled.append("email")

    pans = {p for p in (_clean(f.get("PAN")) for f in folios) if p}
    pan = _clean(next(iter(pans), None), limit=20)
    if pan and not _clean(user.pan):
        clash = (
            await db.execute(select(User.id).where(User.pan == pan, User.id != user_id))
        ).first()
        if clash is None:
            user.pan = pan
            filled.append("pan")

    if filled:
        await db.flush()
        logger.info(
            "CAS ingest: back-filled user %s profile fields %s", user_id, filled
        )
    return filled


def _total_market_value(parsed: dict[str, Any]) -> float:
    """Sum the CAS-stated market value across every scheme in the parsed CAS.

    A cheap pre-write screen for empty statements only — the persisted portfolio
    numbers are transaction-derived (see :func:`_derive_scheme_snapshot`).
    """
    total = 0.0
    for folio in parsed.get("folios") or []:
        for scheme in folio.get("schemes") or []:
            value = _num((scheme.get("valuation") or {}).get("value"))
            if value > 0:
                total += value
    return total


async def _archive_statement_pdf(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    file_bytes: bytes,
    source_filename: Optional[str],
    parsed: dict[str, Any],
    schemes: int,
    transactions: int,
) -> Optional[uuid.UUID]:
    """Best-effort: S3-archive the parsed statement + add its profile record.

    Returns the ``user_cas_documents`` id so the snapshot can point at its PDF,
    or None when archiving is disabled or failed. The row rides the caller's
    final commit; an S3 failure only logs (the import itself must never fail
    because archival did)."""
    if not stage_enabled():
        return None
    try:
        doc_id = uuid.uuid4()
        s3_key = await archive_cas_pdf(user_id, doc_id, file_bytes)
        period = parsed.get("statement_period") or {}
        db.add(
            UserCasDocument(
                id=doc_id,
                user_id=user_id,
                s3_key=s3_key,
                source_filename=_clean(source_filename, limit=255),
                file_size_bytes=len(file_bytes),
                cas_type=_clean(parsed.get("cas_type"), limit=20),
                file_type=_clean(parsed.get("file_type"), limit=30),
                statement_from=_clean(period.get("from"), limit=20),
                statement_to=_clean(period.get("to"), limit=20),
                folios=len(parsed.get("folios") or []),
                schemes=schemes,
                transactions=transactions,
            )
        )
        return doc_id
    except Exception:  # noqa: BLE001
        logger.exception("could not archive the CAS PDF (upload continues)")
        return None


# --------------------------------------------------------------------------- entry point


async def ingest_cams_pdf(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    file_bytes: bytes,
    password: str,
    source_filename: Optional[str] = None,
    replace_existing: bool = False,
) -> CamsIngestResult:
    """Parse a CAMS/KFintech CAS PDF and persist it as a new snapshot.

    A CAS is a *complete* snapshot of the user's mutual-fund holdings, so an upload
    has to fully replace what the app shows. It does that by SUPERSEDING, not by
    deleting: the statement gets a ``cas_uploads`` row, everything derived from it
    is stamped with that row's id, and the previous upload is marked superseded.
    Reads are scoped to the active snapshot (``app/core/cas_scope.py``), so the app
    shows exactly the newest statement while every earlier one stays queryable —
    which is what makes allocation drift, net-worth history and plan-vs-actual
    answerable at all.

    Three things happen before any of that, in this order, and all three matter:

      1. an upload of the byte-identical PDF that is already active returns the
         existing snapshot untouched (no second paid parse, no duplicate row);
      2. Summary and zero-value statements are rejected before any DB write;
      3. a user whose data predates this feature has it adopted into a "legacy"
         snapshot first, so the old rows can never be summed with the new ones.

    With ``CAS_SNAPSHOT_VERSIONING=false`` this falls back to the original
    behaviour: :func:`reset_user_financial_data` wipes the user's financial and
    computed footprint and the statement is rebuilt into the vacuum.

    Profile/onboarding, goals, chats, notifications, account links and family
    relationships are untouched either way; global reference data (fund metadata,
    NAV history, benchmarks) is never touched.

    ``replace_existing`` is retained for API compatibility and does not change
    behaviour.
    """
    versioning = versioning_enabled()
    content_sha256 = cas_upload_service.sha256_of(file_bytes)

    if versioning:
        identical = await cas_upload_service.find_identical_active(
            db, user_id, content_sha256
        )
        if identical is not None:
            logger.info(
                "cams upload is byte-identical to active snapshot %s (user %s) — reusing",
                identical.id,
                user_id,
            )
            return _result_from_snapshot(identical)

    parsed = await _parse_cas_via_api(file_bytes, source_filename, password)

    # Reject statement variants we can't build a real portfolio from — BEFORE any
    # DB writes, and before the reset wipes the user's prior data:
    #   * a Summary CAS has holdings but NO transaction history; and
    #   * a statement with no current holdings (net value 0) has nothing invested.
    if (_clean(parsed.get("cas_type")) or "").upper() == "SUMMARY":
        raise CamsPdfParseError(
            "This is a Summary CAMS / KFintech statement (holdings only, with no "
            "transaction history). Please generate and upload the Detailed "
            "Consolidated Account Statement instead."
        )
    if _total_market_value(parsed) <= 0:
        raise CamsPdfParseError(
            "This statement shows no current mutual-fund holdings (net value ₹0). "
            "Please upload a statement that includes your active investments."
        )

    if not versioning:
        # Legacy path: a CAS fully replaces prior MF state, so wipe all
        # derived/computed data (keeping profile, goals, chats) and rebuild from
        # scratch. Kept behind the flag as the one-step rollback.
        await reset_user_financial_data(db, user_id)
        return await _persist_parsed_cas(
            db,
            user_id,
            parsed=parsed,
            file_bytes=file_bytes,
            source_filename=source_filename,
            snapshot=None,
        )

    # Adopt anything predating this feature into its own snapshot BEFORE minting
    # the new one. Unstamped rows are visible under every scope (THE NULL RULE),
    # so leaving them would double-count the old statement against the new.
    if await cas_upload_service.get_active(db, user_id) is None:
        await cas_upload_service.ensure_legacy_snapshot(db, user_id)

    snapshot = await cas_upload_service.mint(
        db,
        user_id,
        content_sha256=content_sha256,
        source_filename=source_filename,
        file_size_bytes=len(file_bytes),
    )
    # Commit the header on its own so a failure below is RECORDED (status
    # 'failed') rather than rolled back into nothing — and so the live snapshot,
    # still active at this point, keeps serving the app untouched.
    await db.commit()

    with scoped_to(snapshot.id):
        try:
            return await _persist_parsed_cas(
                db,
                user_id,
                parsed=parsed,
                file_bytes=file_bytes,
                source_filename=source_filename,
                snapshot=snapshot,
            )
        except Exception as exc:  # noqa: BLE001
            await cas_upload_service.mark_failed(db, snapshot.id, repr(exc))
            raise


def _result_from_snapshot(snapshot) -> CamsIngestResult:
    """Rebuild the upload response from a snapshot we already hold (dedupe path)."""
    return CamsIngestResult(
        import_id=snapshot.mf_aa_import_id or snapshot.id,
        cas_upload_id=snapshot.id,
        status="NORMALIZED",
        cas_file_type=snapshot.file_type,
        cas_type=snapshot.cas_type,
        statement_period_from=snapshot.statement_from,
        statement_period_to=snapshot.statement_to,
        folios=int(snapshot.folios or 0),
        schemes=int(snapshot.schemes or 0),
        aa_transactions_parsed=int(snapshot.transactions or 0),
        mf_transactions_inserted=0,
        mf_transactions_skipped_duplicate=0,
        portfolio_allocation_rows=0,
        total_value_inr=float(snapshot.total_value_inr or 0.0),
        reused_existing=True,
    )


async def _persist_parsed_cas(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    parsed: dict[str, Any],
    file_bytes: bytes,
    source_filename: Optional[str],
    snapshot,
) -> CamsIngestResult:
    """Write one parsed statement: audit rows, ledger, roll-up, archive, activate."""
    aa_import = _build_import_row(user_id, parsed, source_filename)
    if snapshot is not None:
        aa_import.cas_upload_id = snapshot.id
    db.add(aa_import)
    scheme_count, txn_count, bucket_value, cost_total = _populate_children(
        aa_import, parsed
    )
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

    alloc_rows, total_value = await _apply_portfolio_rollup(
        db, user_id, bucket_value, cost_total
    )
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    await _sync_mf_portfolio_holdings_from_cas(db, portfolio.id, parsed, total_value)
    profile_fields_filled = await _backfill_user_profile(db, user_id, parsed)

    # Keep the statement itself: archive the PDF to S3 and record it for the
    # profile's "My CAS statements" list. Best-effort — never fails the upload.
    # The row lives in `user_cas_documents`, which (deliberately) survives the
    # per-upload reset, unlike the `mf_aa_imports` audit trail.
    cas_document_id = await _archive_statement_pdf(
        db,
        user_id,
        file_bytes=file_bytes,
        source_filename=source_filename,
        parsed=parsed,
        schemes=scheme_count,
        transactions=txn_count,
    )

    if snapshot is not None:
        # Last write of the ingest: the previous statement stays live until this
        # line, so everything above ran against a user whose app never blinked.
        await cas_upload_service.activate(
            db,
            snapshot,
            cas_type=_clean(parsed.get("cas_type"), limit=20),
            file_type=_clean(parsed.get("file_type"), limit=30),
            statement_from=stmt_from,
            statement_to=stmt_to,
            folios=len(parsed.get("folios") or []),
            schemes=scheme_count,
            transactions=txn_count,
            total_value_inr=round(total_value, 2),
            total_invested_inr=round(cost_total, 2) if cost_total > 0 else None,
            mf_aa_import_id=import_id,
            cas_document_id=cas_document_id,
        )
        # The plans the user is looking at were computed against the statement
        # this one just replaced. They are not deleted — they keep pointing at
        # their own snapshot — but the cashflow projection is marked stale so the
        # next read recomputes it against the new portfolio.
        await mark_cashflow_stale(db, user_id, commit=False)

    return CamsIngestResult(
        import_id=import_id,
        cas_upload_id=(snapshot.id if snapshot is not None else None),
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
        profile_fields_filled=profile_fields_filled,
    )
