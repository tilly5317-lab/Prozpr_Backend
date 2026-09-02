"""Ops endpoints for the VR mirror. Authenticated, and read-mostly.

Deliberately narrow. Everything here reports on or refreshes the ``vr`` schema;
nothing touches ``public``, so no call on this router can alter a user's data.
Write-shaped routes (``/sync``, ``/crosswalk/rebuild``, ``/backfill``) exist
because a vendor mirror needs a hand on it during rollout — they are gated on a
live key and serialized by the same advisory locks the scheduler uses, so a
double-click cannot start two concurrent syncs of one table.

**The API key is never returned by any route here.** ``/status`` reports whether
one is configured, not what it is.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.vr_data.client import VrClient, VrError
from app.domains.vr_data.schema import SYNC_STATE, VR_SCHEMA
from app.domains.vr_data.services import backfill_service, crosswalk_service
from app.domains.vr_data.services.sync_service import (
    enabled_specs,
    run_cycle,
    sync_table,
)
from app.domains.vr_data.specs import all_specs, spec

router = APIRouter(prefix="/vr", tags=["VR Data"])


async def _schema_exists(db: AsyncSession) -> bool:
    return bool(
        (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = :s"
                ),
                {"s": VR_SCHEMA},
            )
        ).scalar()
    )


@router.get("/status")
async def vr_status(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Is VR configured, is the schema present, and how fresh is each table."""
    settings = get_settings()
    installed = await _schema_exists(db)

    rows: list[dict[str, Any]] = []
    if installed:
        rows = [
            dict(r)
            for r in (await db.execute(select(SYNC_STATE))).mappings().all()
        ]
    state_by_table = {r["table_name"]: r for r in rows}

    tables = []
    for s in enabled_specs():
        st = state_by_table.get(s.name, {})
        tables.append(
            {
                "table": s.name,
                "tier": s.tier,
                "sync_mode": s.sync_mode,
                "schedule_hours_ist": list(s.schedule_hours),
                "watermark": st.get("watermark"),
                "last_status": st.get("last_status"),
                "last_success_at": st.get("last_success_at"),
                "last_row_count": st.get("last_row_count"),
                "last_error": st.get("last_error"),
                "vr_update_frequency": s.update_frequency,
            }
        )

    return {
        "api_key_configured": settings.vr_enabled(),
        "sync_scheduler_enabled": settings.vr_sync_enabled(),
        "enabled_tiers": list(settings.get_vr_sync_tiers()),
        "schema_installed": installed,
        "declared_tables": len(all_specs()),
        "enabled_tables": len(tables),
        "rate_limit_per_hour": settings.get_vr_rate_limit_per_hour(),
        "tables": tables,
    }


@router.get("/describe")
async def vr_describe(
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """VR's own list of the tables this key is entitled to, diffed against ours.

    One upstream request, and it settles the scope question empirically:
    ``entitled_not_declared`` is what the contract gives us that we have not
    modelled, and ``declared_not_entitled`` is what we have modelled but cannot
    actually fetch — the list to raise with the vendor.
    """
    async with VrClient() as client:
        if not client.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
            )
        try:
            payload = await client.describe()
        except VrError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"{exc} (vendor_refused={getattr(exc, 'reached_vr', None)})",
            ) from None

    entitled = _table_names_from_describe(payload)
    declared = set(all_specs())
    return {
        "entitled_count": len(entitled),
        "declared_count": len(declared),
        "entitled_not_declared": sorted(entitled - declared),
        "declared_not_entitled": sorted(declared - entitled),
        "both": sorted(declared & entitled),
        "raw": payload if len(str(payload)) < 20_000 else "(truncated)",
    }


def _table_names_from_describe(payload: Any) -> set[str]:
    """Pull table names out of /describe without assuming its exact shape.

    We have never seen a live response, so this walks the structure for
    plausible name fields rather than indexing a key that may not exist.
    """
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("table", "table_name", "name", "tablename"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    names.add(value.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and node.strip():
            names.add(node.strip())

    if isinstance(payload, dict):
        for key in ("tables", "data", "result"):
            if key in payload:
                walk(payload[key])
                break
        else:
            walk(payload)
    else:
        walk(payload)
    return names


@router.get("/probe")
async def vr_probe(
    table: str = Query(..., description="VR table name to test"),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """One cheap live call — ``output=count`` — to prove connectivity.

    Uses count rather than data so a probe costs one request and returns no
    rows. This is the route to hit first from the whitelisted backend: a
    Cloudflare-shaped 403 here means the key or the source IP is wrong, and a
    VR-shaped 403 means the table is outside our contract.
    """
    try:
        spec(table)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None

    async with VrClient() as client:
        if not client.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
            )
        try:
            count = await client.count(table)
        except VrError as exc:
            return {
                "table": table,
                "reachable": False,
                "error": str(exc),
                "vendor_refused_table": getattr(exc, "reached_vr", None),
            }
    return {
        "table": table,
        "reachable": True,
        "rows_in_default_window": count,
        "note": "Default window with no changed-after is VR's last 48 hours.",
    }


@router.post("/sync")
async def trigger_sync(
    table: Optional[str] = Query(None, description="One table, or all enabled"),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Run a sync now. Skips any table another worker is already syncing."""
    if not await _schema_exists(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The vr schema is not installed. Apply "
            "migrations/sql/vr_schema.sql first.",
        )
    if table:
        try:
            spec(table)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
        async with VrClient() as client:
            if not client.configured:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
                )
            results = [await sync_table(db, client, table)]
    else:
        results = await run_cycle(db)

    return {
        "results": [
            {
                "table": r.table,
                "ok": r.ok,
                "rows_written": r.rows_written,
                "pages": r.pages,
                "watermark": r.watermark,
                "coercion_failures": r.coercion_failures,
                "skipped": r.skipped_reason,
                "error": r.error,
                "unknown_fields": r.fields_dropped,
            }
            for r in results
        ]
    }


@router.get("/crosswalk")
async def crosswalk_status(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """How much of our fund universe currently resolves to a VR plan."""
    from app.domains.vr_data.schema import SCHEME_LINK

    if not await _schema_exists(db):
        return {"schema_installed": False, "links": 0}
    by_method = (
        (
            await db.execute(
                select(SCHEME_LINK.c.match_method, func.count()).group_by(
                    SCHEME_LINK.c.match_method
                )
            )
        )
        .all()
    )
    ours = int(
        (
            await db.execute(
                text("SELECT count(*) FROM mf_fund_metadata WHERE is_active")
            )
        ).scalar()
        or 0
    )
    total = sum(count for _, count in by_method)
    return {
        "schema_installed": True,
        "links": total,
        "by_method": {method or "unknown": count for method, count in by_method},
        "our_active_schemes": ours,
        "coverage_pct": round(100.0 * total / ours, 2) if ours else 0.0,
    }


@router.post("/crosswalk/rebuild")
async def crosswalk_rebuild(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    report = await crosswalk_service.rebuild_crosswalk(db)
    return {
        "links": report.total_links,
        "by_isin": report.by_isin,
        "by_amfi_code": report.by_amfi_code,
        "manual_preserved": report.manual_preserved,
        "our_active_schemes": report.our_schemes,
        "coverage_pct": report.coverage_pct,
    }


@router.get("/crosswalk/unresolved")
async def crosswalk_unresolved(
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Our active schemes with no VR plan — the manual-linking worklist."""
    rows = await crosswalk_service.unresolved_schemes(db, limit=limit)
    return {"count": len(rows), "schemes": rows}


@router.get("/backfill/budget")
async def backfill_budget(
    table: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Bulk requests left for this table today. VR allows two, and they do not
    refund — check before starting a historical load."""
    remaining = await backfill_service.budget_remaining(db, table)
    return {"table": table, "remaining_today": remaining, "daily_cap": 2}
