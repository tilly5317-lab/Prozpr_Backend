"""Mutual-fund domain HTTP API (CRUD for MF tables)."""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.mf import (
    aa_imports,
    fund_metadata,
    nav_history,
    portfolio_snapshots,
    sip_mandates,
    transactions,
    user_investment_lists,
)

router = APIRouter(prefix="/mf")

router.include_router(fund_metadata.router)
router.include_router(nav_history.router)
router.include_router(sip_mandates.router)
router.include_router(transactions.router)
router.include_router(portfolio_snapshots.router)
router.include_router(user_investment_lists.router)
router.include_router(aa_imports.router)

__all__ = ["router"]
