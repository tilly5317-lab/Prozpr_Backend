"""HTTP routes for ``mf_nav_history``.

Rows are identified for writes by **(scheme_code, nav_date)** or **(isin, nav_date)** —
the table natural keys — not by internal UUID.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import MfNavHistoryCreate, MfNavHistoryResponse, MfNavHistoryUpdate
from app.services.mf import nav_history_service

router = APIRouter(prefix="/nav-history", tags=["MF Data"])


@router.get("/", response_model=list[MfNavHistoryResponse])
async def list_nav_history(
    db: AsyncSession = Depends(get_db),
    # _user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scheme_code: Optional[str] = Query(None, description="AMFI scheme code"),
    isin: Optional[str] = Query(None, description="ISIN (normalized to uppercase)"),
    nav_date_from: Optional[date] = Query(None),
    nav_date_to: Optional[date] = Query(None),
):
    return await nav_history_service.list_nav_rows(
        db,
        skip=skip,
        limit=limit,
        scheme_code=scheme_code,
        isin=isin,
        nav_date_from=nav_date_from,
        nav_date_to=nav_date_to,
    )


@router.get(
    "/by-scheme/{scheme_code}",
    response_model=MfNavHistoryResponse,
    summary="Get NAV by scheme code",
    description=(
        "Latest NAV for this scheme if ``nav_date`` is omitted; "
        "otherwise the row for that calendar day. "
        "Use PATCH/DELETE on this path with required ``nav_date`` to update or remove that day."
    ),
)
async def get_nav_by_scheme_code(
    scheme_code: str,
    db: AsyncSession = Depends(get_db),
    # _user: CurrentUser = Depends(get_effective_user),
    nav_date: Optional[date] = Query(
        None,
        description="If set, return NAV on this date only; if omitted, return latest available NAV.",
    ),
):
    return await nav_history_service.get_nav_by_scheme(db, scheme_code, nav_date=nav_date)


@router.patch(
    "/by-scheme/{scheme_code}",
    response_model=MfNavHistoryResponse,
    summary="Update NAV row by scheme + date",
    description="Identifies the row by ``scheme_code`` and ``nav_date`` (required query param).",
)
async def update_nav_by_scheme(
    scheme_code: str,
    payload: MfNavHistoryUpdate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
    nav_date: date = Query(
        ...,
        description="Calendar date of this NAV row (required — together with scheme_code it is the row key).",
    ),
):
    return await nav_history_service.update_nav_on_scheme_date(db, scheme_code, nav_date, payload)


@router.delete(
    "/by-scheme/{scheme_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete NAV row by scheme + date",
    description="Identifies the row by ``scheme_code`` and ``nav_date`` (required query param).",
)
async def delete_nav_by_scheme(
    scheme_code: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
    nav_date: date = Query(
        ...,
        description="Calendar date of this NAV row to delete.",
    ),
):
    await nav_history_service.delete_nav_on_scheme_date(db, scheme_code, nav_date)


@router.get(
    "/by-isin/{isin}",
    response_model=MfNavHistoryResponse,
    summary="Get NAV by ISIN",
    description=(
        "Latest NAV for this ISIN if ``nav_date`` is omitted; "
        "otherwise the row for that calendar day. "
        "Use PATCH/DELETE with required ``nav_date`` to update or remove that day."
    ),
)
async def get_nav_by_isin(
    isin: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
    nav_date: Optional[date] = Query(
        None,
        description="If set, return NAV on this date only; if omitted, return latest available NAV.",
    ),
):
    return await nav_history_service.get_nav_by_isin(db, isin, nav_date=nav_date)


@router.patch(
    "/by-isin/{isin}",
    response_model=MfNavHistoryResponse,
    summary="Update NAV row by ISIN + date",
    description="Identifies the row by ``isin`` and ``nav_date`` (required query param).",
)
async def update_nav_by_isin(
    isin: str,
    payload: MfNavHistoryUpdate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
    nav_date: date = Query(
        ...,
        description="Calendar date of this NAV row (required — together with ISIN it is the row key).",
    ),
):
    return await nav_history_service.update_nav_on_isin_date(db, isin, nav_date, payload)


@router.delete(
    "/by-isin/{isin}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete NAV row by ISIN + date",
    description="Identifies the row by ``isin`` and ``nav_date`` (required query param).",
)
async def delete_nav_by_isin(
    isin: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
    nav_date: date = Query(
        ...,
        description="Calendar date of this NAV row to delete.",
    ),
):
    await nav_history_service.delete_nav_on_isin_date(db, isin, nav_date)


@router.post("/", response_model=MfNavHistoryResponse, status_code=status.HTTP_201_CREATED)
async def create_nav_history(
    payload: MfNavHistoryCreate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    return await nav_history_service.create_nav_row(db, payload)
