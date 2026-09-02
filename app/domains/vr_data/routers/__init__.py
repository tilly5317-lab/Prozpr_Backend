"""Ops and per-table routes for the VR mirror."""

from app.domains.vr_data.routers.vr_admin_router import router
from app.domains.vr_data.routers.vr_tables_router import router as tables_router

__all__ = ["router", "tables_router"]
