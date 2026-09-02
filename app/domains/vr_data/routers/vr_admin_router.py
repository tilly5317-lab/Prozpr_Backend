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

import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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

#: Field-filter names we will forward to VR. VR's filter syntax is
#: ``FIELDNAME``, ``FIELDNAME-GREATER-THAN``, ``FIELDNAME-LESS-THAN``; anything
#: outside this shape is rejected rather than passed upstream.
_SAFE_PARAM = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _parse_window(raw: Optional[str], field: str):
    """Accept VR's two documented window formats, reject anything else.

    Parsed here rather than forwarded as a string so a typo fails with a clear
    400 instead of spending one of the 500/hour requests to have VR reject it.
    """
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    for fmt, kind in (("%Y-%m-%d-%H-%M", "datetime"), ("%Y-%m-%d", "date")):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed if kind == "datetime" else parsed.date()
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"{field} must be YYYY-MM-DD or YYYY-MM-DD-HH-MM (IST); got {value!r}",
    )


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


@router.get("/raw/{table}")
async def vr_raw(
    request: Request,
    table: str,
    output: str = Query("data", pattern="^(data|count)$"),
    changed_after: Optional[str] = Query(
        None,
        description="YYYY-MM-DD or YYYY-MM-DD-HH-MM (IST). Max 90 days old; "
        "omit both window params and VR returns the last 48 hours.",
    ),
    changed_before: Optional[str] = Query(
        None, description="Only valid with changed_after; caps the result at 7 days."
    ),
    sort: Optional[str] = Query(None, description="Field name, '-' prefix to reverse."),
    max_rows: int = Query(
        25, ge=1, le=500, description="Truncate the response for readability."
    ),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Read one page of a VR table **live**, to inspect real responses.

    A thin, read-only passthrough. It exists because ``/probe`` returns only a
    count, so there was no way to see an actual VR row without shell access to
    the backend — and the backend is the only address VR allows.

    Any query parameter not named above is forwarded to VR as a field filter
    (VR's syntax is ``FIELDNAME=value``, ``FIELDNAME-GREATER-THAN=value``,
    ``FIELDNAME-LESS-THAN=value``), so ``?plan_id=16014`` works here exactly as
    it does against VR.

    Deliberately constrained:

    * **Allowlisted** to tables in our registry, so the path cannot be used to
      reach an arbitrary upstream URL.
    * **Read-only** — writes nothing, and touches no application table.
    * **Truncated** by ``max_rows``; ``row_count`` reports what VR actually
      sent, so truncation is never mistaken for a small result.
    * Shares the account-wide request budget, so heavy use here starves the
      sync. VR's own guidance is to serve users from our mirror and never from
      their API — this is an operator tool, not a read path.
    """
    try:
        spec_ = spec(table)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None

    reserved = {
        "output",
        "changed_after",
        "changed_before",
        "sort",
        "max_rows",
    }
    filters: dict[str, str] = {}
    for key, value in request.query_params.items():
        if key in reserved:
            continue
        if not _SAFE_PARAM.match(key):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"unsupported filter name: {key!r}"
            )
        filters[key] = value

    after = _parse_window(changed_after, "changed_after")
    before = _parse_window(changed_before, "changed_before")

    async with VrClient() as client:
        if not client.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
            )
        try:
            if output == "count":
                return {
                    "table": table,
                    "output": "count",
                    "count": await client.count(table, changed_after=after),
                }
            page = await client.fetch_page(
                table,
                changed_after=after,
                changed_before=before,
                sort=sort,
                filters=filters or None,
            )
        except VrError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"{exc} (vendor_refused={getattr(exc, 'reached_vr', None)})",
            ) from None

    declared = set(spec_.columns)
    returned = set(page.rows[0]) if page.rows else set()
    return {
        "table": table,
        "tier": spec_.tier,
        "row_count": len(page.rows),
        "returned_rows": min(len(page.rows), max_rows),
        "has_more_pages": bool(page.next_url),
        "fields_declared": len(declared),
        "fields_returned": len(returned),
        # The two lists that matter when comparing a live response to the
        # registry: what we would silently drop, and what we expected but VR
        # did not send.
        "unknown_fields": sorted(returned - declared),
        "missing_fields": sorted(declared - returned),
        "filters_applied": filters,
        "rows": page.rows[:max_rows],
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


@router.put("/crosswalk/link", tags=["VR Crosswalk (editable)"])
async def crosswalk_link_upsert(
    plan_id: str = Query(..., description="Value Research plan_id"),
    scheme_code: str = Query(..., description="Our mf_fund_metadata.scheme_code"),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Pin one plan_id → scheme_code mapping by hand. Survives every rebuild.

    This is the **only** writable table in the `vr` schema, and the only place
    CRUD is meaningful here. `vr.scheme_link` is ours, not the vendor's: the
    automatic pass matches on ISIN then AMFI code and deliberately never on
    scheme name, so a plan it cannot resolve is simply absent. A manual link
    fills that gap and is preserved by `match_method='manual'` when the
    crosswalk is rebuilt.

    Every other `vr.*` table is a mirror upserted on Value Research's own key —
    a row written there is overwritten by the next sync, and a row deleted
    there comes back, so those have no write routes on purpose.
    """
    await crosswalk_service.link_manually(
        db, plan_id=plan_id.strip(), scheme_code=scheme_code.strip()
    )
    return {
        "plan_id": plan_id.strip(),
        "scheme_code": scheme_code.strip(),
        "match_method": "manual",
        "note": "Preserved on crosswalk rebuild.",
    }


@router.delete("/crosswalk/link", tags=["VR Crosswalk (editable)"])
async def crosswalk_link_delete(
    plan_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove one mapping. The next rebuild may re-derive it automatically."""
    from sqlalchemy import delete as sql_delete

    from app.domains.vr_data.schema import SCHEME_LINK

    result = await db.execute(
        sql_delete(SCHEME_LINK).where(SCHEME_LINK.c.plan_id == plan_id.strip())
    )
    await db.commit()
    return {"plan_id": plan_id.strip(), "deleted": result.rowcount or 0}


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
