"""One documented endpoint per Value Research table, generated from the specs.

Swagger lists every VR table by name, in VR's own catalogue groupings, each
carrying its real field reference — rather than one generic endpoint you have
to type a table name into to discover anything.

    GET /vr/live/{table}

**Every parameter is optional.** Called bare, an endpoint returns what VR
returns by default: rows changed in the last 48 hours.

Parameters are built **per table** from its own columns, so each endpoint
offers only filters that mean something for it. ``nav`` offers ``fund``;
``fund_basic_details`` also offers ``amc`` and ``category``;
``fund_returns_annual`` offers ``year``; ``countries`` offers none of them.
That is why the routes are generated rather than hand-written — the parameter
list is derived from the same registry as the schema and the sync.

The named filters are business-shaped (``fund``, ``amc``, ``isin``) and map
onto VR's own column names underneath. Any additional query parameter is still
forwarded verbatim as a raw VR filter, so nothing the vendor supports is
blocked.

**There are no create, update or delete routes here, and that is deliberate.**
These tables are a mirror — ``sync_service`` upserts on Value Research's own
primary key, so a locally inserted or edited row is silently overwritten on the
next sync and a locally deleted row comes back. VR's API is read-only to us, so
there is nowhere to push an edit upstream either. Write endpoints would appear
to work and quietly lose data. The one table where writes *are* meaningful is
``vr.scheme_link`` — our own mapping, where manual links survive a rebuild by
design — and that has real CRUD in ``vr_admin_router``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from inspect import Parameter, Signature
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.dependencies import CurrentUser, get_current_user
from app.domains.vr_data.client import VrClient, VrError, today_ist
from app.domains.vr_data.specs import VrTableSpec, all_specs

# No router-level tag: FastAPI merges it with each route's own tag, which would
# add a redundant extra section alongside the real ones.
router = APIRouter(prefix="/vr")

_SAFE_PARAM = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

#: Query names the endpoints own. Anything else on the query string is passed
#: through to VR as a raw field filter.
_RESERVED = {"changed_in", "sort_by", "newest_first", "limit", "count_only"}

#: VR's catalogue groups, mapped to readable Swagger sections.
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

#: Business-meaningful filters, each mapped to the VR column that implements
#: it. A table offers only the ones whose column it actually has — so ``fund``
#: appears on the 22 tables keyed by plan_id and never on ``countries``.
_IDENTITY_FILTERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("fund", ("plan_id",), "One fund only — a Value Research plan_id, e.g. 16014."),
    ("amc", ("amc_id", "fund_amc_id"), "One AMC only — a Value Research amc_id."),
    ("isin", ("isin_code", "isin", "asset_isin"), "One ISIN, e.g. INF090I01JR0."),
    (
        "category",
        ("sebi_category_id", "category_id", "vr_category_id"),
        "One category only — a category id.",
    ),
    ("subplan", ("subplan_id", "subplan_code"), "One sub-plan only."),
    ("security", ("security_id",), "One underlying security only."),
    ("rta", ("rta_code",), "One RTA code only."),
    ("year", ("year",), "One calendar year, e.g. 2024."),
    ("status", ("status",), "One fund status code only."),
    ("source_table", ("table_name",), "Deletions for one source table only."),
)

#: How far back to ask VR for changed rows. VR serves change windows rather
#: than whole tables, so "everything" here means the widest window the normal
#: endpoint allows — 90 days. Older than that needs the async bulk route.
_WINDOWS: dict[str, Optional[int]] = {
    "last_48_hours": None,  # VR's own default when no window is sent
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 89,
}
ChangedIn = Literal["last_48_hours", "last_7_days", "last_30_days", "last_90_days"]


def _identity_params(spec: VrTableSpec) -> dict[str, tuple[str, str]]:
    """``{query name: (vr column, help text)}`` for the filters this table has."""
    available: dict[str, tuple[str, str]] = {}
    columns = set(spec.columns)
    for name, candidates, help_text in _IDENTITY_FILTERS:
        for column in candidates:
            if column in columns:
                available[name] = (column, f"{help_text} (filters `{column}`)")
                break
    return available


def _window_start(changed_in: str):
    days = _WINDOWS.get(changed_in)
    return None if days is None else today_ist() - timedelta(days=days)


def _extra_filters(request: Request, known: set[str]) -> dict[str, str]:
    """Raw VR filters given on the query string beyond the named parameters."""
    out: dict[str, str] = {}
    for key, value in request.query_params.items():
        if key in _RESERVED or key in known:
            continue
        if not _SAFE_PARAM.match(key):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"unsupported filter name: {key!r}"
            )
        out[key] = value
    return out


def _field_doc(spec: VrTableSpec) -> str:
    """The table's own documentation, rendered into the OpenAPI description."""
    from app.domains.vr_data.specs import _catalog  # noqa: PLC0415

    entry = _catalog().get(spec.name, {})
    identity = _identity_params(spec)
    lines = [
        entry.get("description", "").strip(),
        "",
        "**All parameters are optional.** With none, Value Research returns "
        "rows changed in the last 48 hours.",
        "",
        f"**Primary key:** `{'`, `'.join(spec.primary_key)}`  ",
        f"**Tier:** `{spec.tier}` · **Fields:** {len(spec.columns)}"
        + (
            f" · **VR updates:** {spec.update_frequency}"
            if spec.update_frequency
            else ""
        ),
    ]
    if identity:
        lines += [
            "",
            "**Narrow it down with:** "
            + ", ".join(f"`{n}`" for n in identity)
            + ". Any other VR field name also works as a filter.",
        ]
    else:
        lines += [
            "",
            "This is a small master table with no natural filter — call it bare "
            "to read it.",
        ]
    if spec.rationale:
        lines += ["", f"_Why we take it:_ {spec.rationale}"]
    cols = entry.get("columns", [])
    if cols:
        lines += ["", "**Fields**", "", "| Field | Meaning |", "|---|---|"]
        lines += [
            f"| `{c['name']}` | {c['description'].replace('|', '/')} |" for c in cols
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# route factory
# ---------------------------------------------------------------------------


def _make_live_endpoint(spec: VrTableSpec) -> Callable:
    """Build one endpoint whose parameter list is specific to this table."""
    identity = _identity_params(spec)

    async def read_live(**kw: Any) -> dict[str, Any]:
        request: Request = kw["request"]
        count_only: bool = kw["count_only"]
        changed_in: str = kw["changed_in"]
        limit: int = kw["limit"]
        sort_by: Optional[str] = kw["sort_by"]
        newest_first: bool = kw["newest_first"]

        filters: dict[str, str] = {}
        for param, (column, _help) in identity.items():
            value = kw.get(param)
            if value not in (None, ""):
                filters[column] = str(value)
        filters.update(_extra_filters(request, set(identity)))

        sort = None
        if sort_by:
            if sort_by not in spec.columns:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"cannot sort by {sort_by!r}; {spec.name} has no such field.",
                )
            sort = f"-{sort_by}" if newest_first else sort_by

        after = _window_start(changed_in)
        async with VrClient() as client:
            if not client.configured:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
                )
            try:
                if count_only:
                    return {
                        "table": spec.name,
                        "window": changed_in,
                        "filters_applied": filters,
                        "row_count": await client.count(
                            spec.name, changed_after=after
                        ),
                    }
                page = await client.fetch_page(
                    spec.name,
                    changed_after=after,
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
            "window": changed_in,
            "filters_applied": filters,
            "row_count": len(page.rows),
            "returned_rows": min(len(page.rows), limit),
            "has_more_pages": bool(page.next_url),
            # Drift against our registry, surfaced where someone will see it.
            "unknown_fields": sorted(returned - declared),
            "missing_fields": sorted(declared - returned),
            "rows": page.rows[:limit],
        }

    # FastAPI reads __signature__, which is what lets the parameter list be
    # built per table instead of being one fixed set for all 35.
    params = [Parameter("request", Parameter.KEYWORD_ONLY, annotation=Request)]
    for name, (_column, help_text) in identity.items():
        params.append(
            Parameter(
                name,
                Parameter.KEYWORD_ONLY,
                default=Query(None, description=help_text),
                annotation=Optional[str],
            )
        )
    params += [
        Parameter(
            "changed_in",
            Parameter.KEYWORD_ONLY,
            default=Query(
                "last_48_hours",
                description="How far back to ask Value Research for changed rows. "
                "VR serves change windows rather than whole tables; 90 days is the "
                "widest this endpoint allows.",
            ),
            annotation=ChangedIn,
        ),
        Parameter(
            "sort_by",
            Parameter.KEYWORD_ONLY,
            default=Query(None, description="Field name to sort by."),
            annotation=Optional[str],
        ),
        Parameter(
            "newest_first",
            Parameter.KEYWORD_ONLY,
            default=Query(False, description="Reverse the sort order."),
            annotation=bool,
        ),
        Parameter(
            "limit",
            Parameter.KEYWORD_ONLY,
            default=Query(25, ge=1, le=500, description="How many rows to return."),
            annotation=int,
        ),
        Parameter(
            "count_only",
            Parameter.KEYWORD_ONLY,
            default=Query(
                False, description="Return just how many rows match, not the rows."
            ),
            annotation=bool,
        ),
        Parameter(
            "_user",
            Parameter.KEYWORD_ONLY,
            default=Depends(get_current_user),
            annotation=CurrentUser,
        ),
    ]
    read_live.__signature__ = Signature(params)
    return read_live


def _register() -> None:
    """Build the endpoint for every declared table."""
    from app.domains.vr_data.specs import _catalog  # noqa: PLC0415

    catalog = _catalog()
    for name, spec in sorted(all_specs().items(), key=lambda kv: (kv[1].tier, kv[0])):
        tag = _TAGS.get(catalog.get(name, {}).get("category", ""), "VR · Other")
        router.add_api_route(
            f"/live/{name}",
            _make_live_endpoint(spec),
            methods=["GET"],
            name=f"vr_live_{name}",
            operation_id=f"vr_live_{name}",
            summary=f"{name} — {catalog.get(name, {}).get('description', '')[:90]}",
            description=_field_doc(spec),
            tags=[tag],
            response_model=None,
        )


@router.get("/describe", tags=["VR · Contract & entitlement"])
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


_register()
