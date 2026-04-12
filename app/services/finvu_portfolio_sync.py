"""Application service — `finvu_portfolio_sync.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioAllocation
from app.schemas.ingest.finvu import FinvuPortfolioSyncRequest, FinvuPortfolioSyncResponse
from app.services.portfolio_service import get_or_create_primary_portfolio


async def apply_finvu_bucket_snapshot(
    db: AsyncSession,
    user_id: uuid.UUID,
    payload: FinvuPortfolioSyncRequest,
) -> FinvuPortfolioSyncResponse:
    portfolio = await get_or_create_primary_portfolio(db, user_id)

    amounts: dict[str, float] = {"Cash": 0.0, "Debt": 0.0, "Equity": 0.0, "Other": 0.0}
    for b in payload.buckets:
        amounts[b.bucket] = amounts.get(b.bucket, 0.0) + float(b.value_inr)

    total = sum(amounts.values())
    if total <= 0:
        return FinvuPortfolioSyncResponse(
            portfolio_id=str(portfolio.id),
            total_value_inr=0.0,
            allocation_rows_written=0,
            message="Total bucket value is zero; nothing written.",
        )

    await db.execute(delete(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio.id))

    rows = 0
    for name, raw in amounts.items():
        if raw <= 0:
            continue
        pct = round(100.0 * raw / total, 4)
        db.add(
            PortfolioAllocation(
                portfolio_id=portfolio.id,
                asset_class=name,
                allocation_percentage=pct,
                amount=raw,
            )
        )
        rows += 1

    portfolio.total_value = total
    portfolio.total_invested = total
    await db.flush()

    return FinvuPortfolioSyncResponse(
        portfolio_id=str(portfolio.id),
        total_value_inr=total,
        allocation_rows_written=rows,
        message=(
            f"Synced {rows} allocation row(s) from {payload.source} into primary portfolio "
            f"(total INR {total:,.2f}). Holdings detail should be enriched via SimBanks/MF sync where needed."
        ),
    )
