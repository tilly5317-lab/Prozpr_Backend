"""One documented endpoint pair per Value Research table, generated from specs.

Every table in the registry gets two read routes, so Swagger lists all of them
by name with their real fields instead of one generic passthrough you have to
type a table name into:

``GET /vr/live/{table}``
    Straight from Value Research. What the vendor is serving right now.
``GET /vr/db/{table}``
    From our ``vr`` mirror. What we actually hold, and what every downstream
    feature would read.

Having both side by side is the point: the same table from both sources is how
you tell "VR changed something" from "our sync has not run".

**There are no create, update or delete routes for vendor tables, and that is
deliberate.** These tables are a mirror — ``sync_service`` upserts on Value
Research's own primary key, so a locally inserted or edited row is silently
overwritten on the next sync and a locally deleted row comes back. VR's API is
read-only to us, so there is nowhere to push an edit upstream either. Write
endpoints here would appear to work and quietly lose data. The one table where
writes *are* meaningful is ``vr.scheme_link`` — our own mapping, where manual
links survive a rebuild by design — and that has real CRUD in
``vr_admin_router``.

Routes are built in a loop over ``all_specs()``, so adding a VR table to the
registry adds its endpoints automatically; there is no per-table code to
forget to write.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.vr_data.client import VrClient, VrError
from app.domains.vr_data.schema import table_for
from app.domains.vr_data.specs import VrTableSpec, all_specs

# No router-level tag: FastAPI merges it with each route's own tag, which
# would add a redundant "VR Tables" section alongside the real ones.
router = APIRouter(prefix="/vr")

_SAFE_PARAM = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

#: Query names the endpoints own; anything else is treated as a field filter.
_RESERVED = {
    "output",
    "changed_after",
    "changed_before",
    "sort",
    "max_rows",
    "limit",
    "offset",
    "order_by",
    "descending",
}

#: VR's catalogue groups, mapped to readable Swagger tags so 70 routes stay
#: navigable. Keyed by the ``category`` in catalog.json.
_TAGS = {
    "fund-info": "VR · Fund info & classification",
    "performance": "VR · Performance & returns",
    "risk": "VR · Risk metrics",
    "ratings": "VR · Ratings & ranks",
    "benchmark": "VR · Benchmarks",
    "holdings": "VR · Holdings & look-through",
    "nav": "VR · NAV",
    "actions": "VR · Corporate actions",
    "transactions": "VR · Transactions & operations",
    "reference": "VR · Reference masters",
    "meta": "VR · Change tracking",
}


def _parse_window(raw: Optional[str], field: str):
    """VR's two documented window formats, rejected locally on a typo."""
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    for fmt, is_dt in (("%Y-%m-%d-%H-%M", True), ("%Y-%m-%d", False)):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed if is_dt else parsed.date()
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"{field} must be YYYY-MM-DD or YYYY-MM-DD-HH-MM (IST); got {value!r}",
    )


def _filters_from(request: Request, spec: VrTableSpec) -> dict[str, str]:
    """Unreserved query params, validated as VR field filters."""
    out: dict[str, str] = {}
    for key, value in request.query_params.items():
        if key in _RESERVED:
            continue
        if not _SAFE_PARAM.match(key):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"unsupported filter name: {key!r}"
            )
        out[key] = value
    return out


def _field_doc(spec: VrTableSpec, *, with_fields: bool) -> str:
    """The table's own documentation, rendered into the OpenAPI description.

    The full field table is attached to the ``/live/`` route only. Repeating 538
    field rows on both routes doubled the generated OpenAPI document and cost
    seconds on the first ``/docs`` load for no extra information.
    """
    from app.domains.vr_data.specs import _catalog  # noqa: PLC0415

    entry = _catalog().get(spec.name, {})
    lines = [
        entry.get("description", "").strip(),
        "",
        f"**Primary key:** `{'`, `'.join(spec.primary_key)}`  ",
        f"**Tier:** `{spec.tier}` · **Fields:** {len(spec.columns)}"
        + (
            f" · **VR updates:** {spec.update_frequency}"
            if spec.update_frequency
            else ""
        ),
    ]
    if spec.rationale:
        lines += ["", f"_Why we take it:_ {spec.rationale}"]
    cols = entry.get("columns", []) if with_fields else []
    if not with_fields:
        lines += ["", f"Field reference: see `GET /vr/live/{spec.name}`."]
    if cols:
        lines += ["", "**Fields**", "", "| Field | Meaning |", "|---|---|"]
        lines += [
            f"| `{c['name']}` | {c['description'].replace('|', '/')} |" for c in cols
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# route factories
# ---------------------------------------------------------------------------


def _make_live_endpoint(spec: VrTableSpec) -> Callable:
    async def read_live(
        request: Request,
        output: str = Query("data", pattern="^(data|count)$"),
        changed_after: Optional[str] = Query(
            None,
            description="YYYY-MM-DD or YYYY-MM-DD-HH-MM (IST). Max 90 days old. "
            "Omit both window params and VR returns the last 48 hours.",
        ),
        changed_before: Optional[str] = Query(
            None, description="Only valid with changed_after; caps the result at 7 days."
        ),
        sort: Optional[str] = Query(
            None, description="Field name; prefix with '-' to reverse."
        ),
        max_rows: int = Query(25, ge=1, le=500),
        _: CurrentUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        after = _parse_window(changed_after, "changed_after")
        before = _parse_window(changed_before, "changed_before")
        filters = _filters_from(request, spec)

        async with VrClient() as client:
            if not client.configured:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
                )
            try:
                if output == "count":
                    return {
                        "table": spec.name,
                        "source": "value-research",
                        "output": "count",
                        "count": await client.count(spec.name, changed_after=after),
                    }
                page = await client.fetch_page(
                    spec.name,
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

        declared = set(spec.columns)
        returned = set(page.rows[0]) if page.rows else set()
        return {
            "table": spec.name,
            "source": "value-research",
            "row_count": len(page.rows),
            "returned_rows": min(len(page.rows), max_rows),
            "has_more_pages": bool(page.next_url),
            # Drift against the registry, reported where someone will see it.
            "unknown_fields": sorted(returned - declared),
            "missing_fields": sorted(declared - returned),
            "filters_applied": filters,
            "rows": page.rows[:max_rows],
        }

    return read_live


def _make_db_endpoint(spec: VrTableSpec) -> Callable:
    async def read_mirror(
        request: Request,
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        order_by: Optional[str] = Query(
            None, description="Column to sort by. Defaults to the primary key."
        ),
        descending: bool = Query(False),
        db: AsyncSession = Depends(get_db),
        _: CurrentUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        target = table_for(spec.name)
        filters = _filters_from(request, spec)

        unknown = [c for c in filters if c not in spec.columns]
        if unknown:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{spec.name} has no column(s) {unknown}. "
                f"Valid: {', '.join(sorted(spec.columns))}",
            )
        if order_by and order_by not in spec.columns:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"cannot order by {order_by!r}; not a column of {spec.name}",
            )

        stmt = select(target)
        for column, value in filters.items():
            stmt = stmt.where(target.c[column] == value)
        sort_col = target.c[order_by] if order_by else target.c[spec.primary_key[0]]
        stmt = stmt.order_by(sort_col.desc() if descending else sort_col.asc())

        try:
            rows = (
                (await db.execute(stmt.limit(limit).offset(offset))).mappings().all()
            )
        except Exception as exc:  # noqa: BLE001 - the schema may not exist yet
            await db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Could not read vr.{spec.name} ({type(exc).__name__}). If the "
                "schema is not installed, run: "
                "venv/bin/python -m scripts.vr_bootstrap --apply",
            ) from None

        return {
            "table": spec.name,
            "source": "mirror",
            "returned_rows": len(rows),
            "limit": limit,
            "offset": offset,
            "filters_applied": filters,
            "rows": [dict(r) for r in rows],
        }

    return read_mirror


def _register() -> None:
    """Build both routes for every declared table."""
    from app.domains.vr_data.specs import _catalog  # noqa: PLC0415

    catalog = _catalog()
    for name, spec in sorted(
        all_specs().items(), key=lambda kv: (kv[1].tier, kv[0])
    ):
        tag = _TAGS.get(catalog.get(name, {}).get("category", ""), "VR · Other")

        router.add_api_route(
            f"/live/{name}",
            _make_live_endpoint(spec),
            methods=["GET"],
            name=f"vr_live_{name}",
            operation_id=f"vr_live_{name}",
            summary=f"{name} — live from Value Research",
            description=_field_doc(spec, with_fields=True),
            tags=[tag],
            response_model=None,
        )
        router.add_api_route(
            f"/db/{name}",
            _make_db_endpoint(spec),
            methods=["GET"],
            name=f"vr_db_{name}",
            operation_id=f"vr_db_{name}",
            summary=f"{name} — from our mirror",
            description=_field_doc(spec, with_fields=False),
            tags=[tag],
            response_model=None,
        )


_register()
