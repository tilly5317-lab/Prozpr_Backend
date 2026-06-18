"""HTTP routes for benchmark indices (catalogue + EOD value history).

Benchmark data is global (not user-scoped); endpoints require auth but are not
family-member scoped.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.benchmarks.schemas import (
    BenchmarkHistoryResponse,
    BenchmarkIndexSummary,
    BenchmarkValueRow,
)
from app.domains.benchmarks.services import benchmark_data_service as svc

router = APIRouter(prefix="/benchmarks", tags=["Benchmarks"])


@router.get("", response_model=list[BenchmarkIndexSummary])
async def list_benchmarks(
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
    active_only: bool = Query(False, description="Only indices the scheduler refreshes"),
):
    """List benchmark indices in the catalogue, each with its latest EOD value."""
    indices = await svc.list_indices(db, active_only=active_only)
    out: list[BenchmarkIndexSummary] = []
    for index in indices:
        latest = await svc.get_latest_value(db, index.code)
        out.append(
            BenchmarkIndexSummary(
                id=index.id,
                code=index.code,
                display_name=index.display_name,
                short_name=index.short_name,
                provider=index.provider,
                asset_class=index.asset_class,
                description=index.description,
                earliest_available=index.earliest_available,
                is_active=index.is_active,
                latest_value=float(latest.tri_value) if latest else None,
                latest_value_date=latest.value_date if latest else None,
                created_at=index.created_at,
            )
        )
    return out


@router.get("/{code}/history", response_model=BenchmarkHistoryResponse)
async def get_benchmark_history(
    code: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
):
    """EOD value series for one index, optionally clipped to [from, to]."""
    index = await svc.get_index_by_code(db, code)
    if index is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown benchmark index '{code}'",
        )
    series = await svc.load_value_series(db, code)
    points = [
        BenchmarkValueRow(value_date=d, tri_value=v)
        for d, v in series
        if (date_from is None or d >= date_from) and (date_to is None or d <= date_to)
    ]
    return BenchmarkHistoryResponse(
        code=index.code, display_name=index.display_name, points=points
    )


@router.get("/{code}/latest", response_model=BenchmarkValueRow)
async def get_benchmark_latest(
    code: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
):
    """Latest available EOD value for one index."""
    index = await svc.get_index_by_code(db, code)
    if index is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown benchmark index '{code}'",
        )
    latest = await svc.get_latest_value(db, code)
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No values stored yet for '{code}'",
        )
    return BenchmarkValueRow.model_validate(latest)
