"""Application service — `mf_aa_normalizer.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Iterable, Optional

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.mf import (
    MfAaImport,
    MfAaImportStatus,
    MfFundMetadata,
    MfOptionType,
    MfPlanType,
    MfTransaction,
    MfTransactionSource,
    MfTransactionType,
)


@dataclass(frozen=True)
class NormalizeResult:
    import_id: uuid.UUID
    inserted: int
    skipped_duplicate: int
    status: MfAaImportStatus
    error: Optional[str] = None


def _clean(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).replace(",", "").strip()
    if text == "":
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _parse_date(value: object) -> date:
    text = _clean(value)
    if not text:
        return datetime.now(timezone.utc).date()
    fmts = ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d")
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return datetime.now(timezone.utc).date()


def _map_transaction_type(flag: Optional[str], desc: Optional[str], amount: float) -> MfTransactionType:
    f = (flag or "").upper()
    d = (desc or "").upper()
    if f in {"SO", "SWITCH_OUT"}:
        return MfTransactionType.SWITCH_OUT
    if f in {"SI", "SWITCH_IN"}:
        return MfTransactionType.SWITCH_IN
    if f in {"R", "SELL", "REDEMPTION"}:
        return MfTransactionType.SELL
    if f in {"P", "BUY", "SIP"}:
        return MfTransactionType.BUY
    if "SWITCH OUT" in d:
        return MfTransactionType.SWITCH_OUT
    if "SWITCH IN" in d:
        return MfTransactionType.SWITCH_IN
    if "DIVIDEND" in d:
        return MfTransactionType.DIVIDEND_REINVEST
    if "REDEMPTION" in d:
        return MfTransactionType.SELL
    return MfTransactionType.SELL if amount < 0 else MfTransactionType.BUY


def _build_fingerprint(
    *,
    user_id: uuid.UUID,
    scheme_code: str,
    folio_number: str,
    transaction_type: MfTransactionType,
    transaction_date: date,
    units: float,
    nav: float,
    amount: float,
) -> str:
    raw = "|".join(
        [
            str(user_id),
            scheme_code,
            folio_number,
            transaction_type.value,
            transaction_date.isoformat(),
            f"{units:.6f}",
            f"{nav:.6f}",
            f"{amount:.2f}",
        ]
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _to_scheme_code(txn_or_summary: object) -> Optional[str]:
    scheme = _clean(getattr(txn_or_summary, "scheme", None))
    if scheme:
        return scheme[:20]
    isin = _clean(getattr(txn_or_summary, "isin", None))
    if isin:
        return isin[:20]
    return None


async def _upsert_metadata(db: AsyncSession, aa_import: MfAaImport) -> None:
    scheme_codes = {
        _to_scheme_code(row)
        for row in [*aa_import.summaries, *aa_import.transactions]
        if _to_scheme_code(row)
    }
    if not scheme_codes:
        return

    existing = (
        await db.execute(select(MfFundMetadata).where(MfFundMetadata.scheme_code.in_(scheme_codes)))
    ).scalars().all()
    by_code = {row.scheme_code: row for row in existing}

    for summary in aa_import.summaries:
        code = _to_scheme_code(summary)
        if not code:
            continue
        category = (summary.asset_type or "OTHER").strip()[:50]
        scheme_name = (summary.scheme_name or code).strip()[:200]
        amc_name = (summary.amc_name or "Unknown AMC").strip()[:100]
        sub_category = None

        row = by_code.get(code)
        if row:
            row.scheme_name = row.scheme_name or scheme_name
            row.amc_name = row.amc_name or amc_name
            row.category = row.category or category
            row.sub_category = row.sub_category or sub_category
            continue

        row = MfFundMetadata(
            scheme_code=code,
            scheme_name=scheme_name,
            amc_name=amc_name,
            category=category,
            sub_category=sub_category,
            plan_type=MfPlanType.REGULAR,
            option_type=MfOptionType.GROWTH,
            is_active=True,
        )
        db.add(row)
        by_code[code] = row


async def normalize_single_import(db: AsyncSession, aa_import: MfAaImport) -> NormalizeResult:
    if aa_import.user_id is None:
        aa_import.status = MfAaImportStatus.FAILED
        aa_import.failure_reason = "user_id_missing"
        await db.commit()
        return NormalizeResult(
            import_id=aa_import.id,
            inserted=0,
            skipped_duplicate=0,
            status=MfAaImportStatus.FAILED,
            error="user_id_missing",
        )

    aa_import.status = MfAaImportStatus.NORMALIZING
    aa_import.failure_reason = None
    await db.flush()

    try:
        await _upsert_metadata(db, aa_import)
        await db.flush()

        pending_rows: list[tuple[MfTransaction, str]] = []
        for row in aa_import.transactions:
            scheme_code = _to_scheme_code(row)
            if not scheme_code:
                continue

            folio_number = (_clean(row.folio) or "UNKNOWN")[:30]
            tx_date = _parse_date(row.trxn_date or row.posted_date)
            units = _to_float(row.trxn_units, default=0.0)
            nav = _to_float(row.purchase_price, default=0.0)
            amount = _to_float(row.trxn_amount, default=0.0)
            tx_type = _map_transaction_type(row.trxn_type_flag, row.trxn_desc, amount)

            fp = _build_fingerprint(
                user_id=aa_import.user_id,
                scheme_code=scheme_code,
                folio_number=folio_number,
                transaction_type=tx_type,
                transaction_date=tx_date,
                units=units,
                nav=nav,
                amount=amount,
            )
            pending_rows.append(
                (
                    MfTransaction(
                        user_id=aa_import.user_id,
                        scheme_code=scheme_code,
                        sip_mandate_id=None,
                        folio_number=folio_number,
                        transaction_type=tx_type,
                        transaction_date=tx_date,
                        units=units,
                        nav=nav,
                        amount=amount,
                        stamp_duty=_to_float(row.stamp_duty, default=0.0),
                        source_system=MfTransactionSource.AA,
                        source_import_id=aa_import.id,
                        source_txn_fingerprint=fp,
                    ),
                    fp,
                )
            )

        fingerprints = [fp for _, fp in pending_rows]
        existing_fps: set[str] = set()
        if fingerprints:
            existing_fps = set(
                (
                    await db.execute(
                        select(MfTransaction.source_txn_fingerprint).where(
                            MfTransaction.source_system == MfTransactionSource.AA,
                            MfTransaction.source_txn_fingerprint.in_(fingerprints),
                        )
                    )
                ).scalars().all()
            )

        inserted = 0
        skipped = 0
        for txn, fp in pending_rows:
            if fp in existing_fps:
                skipped += 1
                continue
            db.add(txn)
            inserted += 1

        aa_import.status = MfAaImportStatus.NORMALIZED
        aa_import.normalized_at = datetime.now(timezone.utc)
        aa_import.failure_reason = None
        await db.commit()
        return NormalizeResult(
            import_id=aa_import.id,
            inserted=inserted,
            skipped_duplicate=skipped,
            status=MfAaImportStatus.NORMALIZED,
        )
    except Exception as exc:
        await db.rollback()
        aa_import.status = MfAaImportStatus.FAILED
        aa_import.failure_reason = str(exc)[:255]
        await db.commit()
        return NormalizeResult(
            import_id=aa_import.id,
            inserted=0,
            skipped_duplicate=0,
            status=MfAaImportStatus.FAILED,
            error=str(exc),
        )


def _pending_imports_stmt(user_id: uuid.UUID, limit: int) -> Select[tuple[MfAaImport]]:
    return (
        select(MfAaImport)
        .where(
            MfAaImport.user_id == user_id,
            MfAaImport.status.in_([MfAaImportStatus.RECEIVED, MfAaImportStatus.FAILED]),
        )
        .options(
            selectinload(MfAaImport.transactions),
            selectinload(MfAaImport.summaries),
        )
        .order_by(MfAaImport.imported_at.asc())
        .limit(limit)
    )


async def normalize_pending_imports(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int = 25
) -> list[NormalizeResult]:
    imports = (await db.execute(_pending_imports_stmt(user_id, limit))).scalars().all()
    results: list[NormalizeResult] = []
    for aa_import in imports:
        results.append(await normalize_single_import(db, aa_import))
    return results


async def get_import_for_user(
    db: AsyncSession, user_id: uuid.UUID, import_id: uuid.UUID
) -> Optional[MfAaImport]:
    return (
        await db.execute(
            select(MfAaImport)
            .where(MfAaImport.id == import_id, MfAaImport.user_id == user_id)
            .options(
                selectinload(MfAaImport.transactions),
                selectinload(MfAaImport.summaries),
            )
        )
    ).scalar_one_or_none()
