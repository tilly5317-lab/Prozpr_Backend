"""Mutual-fund domain HTTP API (CRUD for MF tables)."""

from __future__ import annotations

from fastapi import APIRouter

from app.domains.mutual_funds.routers import (
    aa_imports_router,
    fund_metadata_router,
    fund_rating_router,
    holding_detail_router,
    latest_snapshot_router,
    nav_history_router,
    portfolio_snapshots_router,
    sip_mandates_router,
    transactions_router,
    user_investment_lists_router,
)

router = APIRouter(prefix="/mf")

router.include_router(fund_metadata_router.router)
router.include_router(fund_rating_router.router)
router.include_router(holding_detail_router.router)
router.include_router(latest_snapshot_router.router)
router.include_router(nav_history_router.router)
router.include_router(sip_mandates_router.router)
router.include_router(transactions_router.router)
router.include_router(portfolio_snapshots_router.router)
router.include_router(user_investment_lists_router.router)
router.include_router(aa_imports_router.router)

__all__ = ["router"]
