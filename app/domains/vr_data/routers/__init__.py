"""Ops, per-table, browse and JSON-query routes for the VR mirror."""

from app.domains.vr_data.routers.vr_admin_router import router
from app.domains.vr_data.routers.vr_browse_router import router as browse_router
from app.domains.vr_data.routers.vr_query_router import router as query_router
from app.domains.vr_data.routers.vr_tables_router import router as tables_router

__all__ = ["router", "tables_router", "browse_router", "query_router"]
