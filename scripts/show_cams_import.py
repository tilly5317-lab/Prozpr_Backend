"""Dev-only: dump the DB rows produced by a CAMS / KFintech CAS PDF upload.

Run after `POST /api/v1/mf-ingest/cams-pdf` to see exactly what landed in
`mf_aa_imports` / `mf_aa_summaries` / `mf_aa_transactions` / `mf_fund_metadata`
/ `mf_transactions` / `portfolio_allocations`.

    python scripts/show_cams_import.py                 # latest mf_aa_imports row
    python scripts/show_cams_import.py <import_id>     # a specific import
    python scripts/show_cams_import.py --user <user_id>  # latest import for that user

Reads `DATABASE_URL` from the environment / .env (same as the app).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import _get_session_factory, dispose_engine
from app.models.mf import (
    MfAaImport,
    MfFundMetadata,
    MfTransaction,
    MfTransactionSource,
)
from app.models.portfolio import Portfolio, PortfolioAllocation


def _fmt(value: object) -> str:
    if value is None:
        return "NULL"
    return str(value)


def _table(title: str, headers: list[str], rows: list[list[object]]) -> None:
    print(f"\n=== {title} ({len(rows)} row{'s' if len(rows) != 1 else ''}) ===")
    if not rows:
        print("  (none)")
        return
    str_rows = [[_fmt(v) for v in r] for r in rows]
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in str_rows)) for i in range(len(headers))
    ]
    print("  " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in str_rows:
        print("  " + " | ".join(r[i].ljust(widths[i]) for i in range(len(headers))))


async def _resolve_import(db, arg: str | None, user_arg: str | None) -> MfAaImport | None:
    stmt = select(MfAaImport).options(
        selectinload(MfAaImport.summaries),
        selectinload(MfAaImport.transactions),
    )
    if arg:
        stmt = stmt.where(MfAaImport.id == uuid.UUID(arg))
    elif user_arg:
        stmt = stmt.where(MfAaImport.user_id == uuid.UUID(user_arg)).order_by(MfAaImport.imported_at.desc())
    else:
        stmt = stmt.order_by(MfAaImport.imported_at.desc())
    return (await db.execute(stmt.limit(1))).scalars().first()


async def _run(arg: str | None, user_arg: str | None) -> None:
    factory = _get_session_factory()
    async with factory() as db:
        imp = await _resolve_import(db, arg, user_arg)
        if not imp:
            print("No mf_aa_imports rows found.")
            return

        _table(
            "mf_aa_imports",
            ["id", "user_id", "pan", "email", "mobile", "from_date", "to_date",
             "first", "middle", "last", "source_file", "status", "normalized_at", "failure_reason"],
            [[imp.id, imp.user_id, imp.pan, imp.email, imp.mobile, imp.from_date, imp.to_date,
              imp.investor_first_name, imp.investor_middle_name, imp.investor_last_name,
              imp.source_file, imp.status.value, imp.normalized_at, imp.failure_reason]],
        )

        _table(
            "mf_aa_summaries",
            ["row_no", "amc_name", "asset_type", "folio", "isin", "scheme", "scheme_name",
             "closing_balance", "cost_value", "market_value", "nav", "last_nav_date", "last_trxn_date", "rta_code"],
            [[s.row_no, s.amc_name, s.asset_type, s.folio, s.isin, s.scheme, s.scheme_name,
              s.closing_balance, s.cost_value, s.market_value, s.nav, s.last_nav_date, s.last_trxn_date, s.rta_code]
             for s in sorted(imp.summaries, key=lambda x: x.row_no)],
        )

        _table(
            "mf_aa_transactions",
            ["row_no", "folio", "isin", "scheme", "scheme_name", "posted_date", "trxn_date",
             "trxn_amount", "trxn_units", "purchase_price", "trxn_desc", "trxn_type_flag"],
            [[t.row_no, t.folio, t.isin, t.scheme, t.scheme_name, t.posted_date, t.trxn_date,
              t.trxn_amount, t.trxn_units, t.purchase_price, t.trxn_desc, t.trxn_type_flag]
             for t in sorted(imp.transactions, key=lambda x: x.row_no)],
        )

        scheme_codes = sorted({s.scheme for s in imp.summaries if s.scheme})
        meta = (
            await db.execute(select(MfFundMetadata).where(MfFundMetadata.scheme_code.in_(scheme_codes)))
        ).scalars().all() if scheme_codes else []
        _table(
            "mf_fund_metadata (for this import's scheme codes)",
            ["scheme_code", "scheme_name", "amc_name", "category", "sub_category", "isin", "plan_type", "option_type", "is_active"],
            [[m.scheme_code, m.scheme_name, m.amc_name, m.category, m.sub_category, m.isin,
              getattr(m.plan_type, "value", m.plan_type), getattr(m.option_type, "value", m.option_type), m.is_active]
             for m in meta],
        )

        mtx = (
            await db.execute(
                select(MfTransaction)
                .where(MfTransaction.source_import_id == imp.id, MfTransaction.source_system == MfTransactionSource.AA)
                .order_by(MfTransaction.transaction_date.asc())
            )
        ).scalars().all()
        _table(
            "mf_transactions (source_system=AA, source_import_id=this)",
            ["scheme_code", "folio_number", "transaction_type", "transaction_date", "units", "nav", "amount",
             "isin", "fund_name", "category", "source_txn_fingerprint"],
            [[t.scheme_code, t.folio_number, getattr(t.transaction_type, "value", t.transaction_type),
              t.transaction_date, t.units, t.nav, t.amount, t.isin, t.fund_name, t.category,
              (t.source_txn_fingerprint or "")[:16] + "…" if t.source_txn_fingerprint else None]
             for t in mtx],
        )

        if imp.user_id:
            portfolio = (
                await db.execute(select(Portfolio).where(Portfolio.user_id == imp.user_id, Portfolio.is_primary == True))  # noqa: E712
            ).scalars().first()
            if portfolio:
                _table(
                    "portfolios (primary)",
                    ["id", "name", "is_primary", "total_value", "total_invested"],
                    [[portfolio.id, portfolio.name, portfolio.is_primary, portfolio.total_value, portfolio.total_invested]],
                )
                allocs = (
                    await db.execute(select(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio.id))
                ).scalars().all()
                _table(
                    "portfolio_allocations",
                    ["asset_class", "amount", "allocation_percentage"],
                    [[a.asset_class, a.amount, a.allocation_percentage] for a in allocs],
                )

    await dispose_engine()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a]
    import_arg: str | None = None
    user_arg: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--user" and i + 1 < len(args):
            user_arg = args[i + 1]
            i += 2
        else:
            import_arg = args[i]
            i += 1
    asyncio.run(_run(import_arg, user_arg))
