"""One JSON-in endpoint for querying any Value Research table.

The per-table `GET` routes are for discovery — they document each table's real
fields and offer only the filters that apply to it. This is the same capability
with a JSON body instead of a row of query boxes, which is easier to edit,
paste and keep around while testing:

    POST /vr/query
    {
      "table": "fund_basic_details",
      "fund": "16014",
      "changed_in": "last_90_days",
      "limit": 50
    }

Everything except ``table`` is optional. The named filters are the same
business-shaped ones the GET routes use and map onto VR's own columns
underneath; ``filters`` takes any additional VR field name verbatim, so nothing
the vendor supports is blocked.

Filters are validated against the named table's own columns, so asking for
``year`` on ``nav`` returns a 400 naming the filters that table *does* accept —
rather than being forwarded to VR and silently returning nothing.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, get_current_user
from app.domains.vr_data.client import VrClient, VrError
from app.domains.vr_data.routers.vr_tables_router import (
    ChangedIn,
    _identity_params,
    _window_start,
)
from app.domains.vr_data.specs import all_specs, spec

router = APIRouter(prefix="/vr", tags=["VR · Query (JSON)"])


class VrQuery(BaseModel):
    """A query against one Value Research table."""

    table: str = Field(
        ...,
        description="VR table name. See `GET /vr/describe` for the full list.",
    )

    # Business-shaped filters. Only those that exist on the chosen table are
    # accepted; the error names which ones do.
    fund: Optional[str] = Field(None, description="plan_id — from `GET /vr/funds`.")
    amc: Optional[str] = Field(None, description="amc_id — from `GET /vr/amcs`.")
    isin: Optional[str] = Field(None, description="ISIN, 12 characters.")
    category: Optional[str] = Field(
        None, description="Category id — from `GET /vr/categories`."
    )
    subplan: Optional[str] = None
    security: Optional[str] = None
    rta: Optional[str] = None
    year: Optional[str] = None
    status_code: Optional[str] = Field(
        None, alias="status", description="Fund status code."
    )
    source_table: Optional[str] = Field(
        None, description="For deleted_logs: the table a deletion came from."
    )

    changed_in: ChangedIn = Field(
        "last_48_hours",
        description="How far back to ask VR for changed rows. VR serves change "
        "windows rather than whole tables; 90 days is the widest allowed here.",
    )
    sort_by: Optional[str] = Field(None, description="Field name to sort by.")
    newest_first: bool = False
    limit: int = Field(25, ge=1, le=500)
    count_only: bool = Field(
        False, description="Return just how many rows match, not the rows."
    )
    filters: dict[str, str] = Field(
        default_factory=dict,
        description="Any additional VR field filters, verbatim — e.g. "
        '{"amfi_code": "120716"}.',
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "table": "fund_basic_details",
                    "fund": "16014",
                    "changed_in": "last_90_days",
                    "limit": 5,
                },
                {
                    "table": "nav",
                    "fund": "16014",
                    "changed_in": "last_30_days",
                    "sort_by": "nav_date",
                    "newest_first": True,
                    "limit": 10,
                },
                {
                    "table": "fund_holdings_details",
                    "fund": "16014",
                    "changed_in": "last_90_days",
                    "limit": 50,
                },
                {"table": "amcs", "changed_in": "last_90_days", "limit": 200},
                {"table": "nav", "changed_in": "last_7_days", "count_only": True},
            ]
        },
    }


#: Request field -> the identity filter name used by the table registry.
_ALIASES = {"status_code": "status"}


@router.post("/query", summary="Query any VR table with a JSON body")
async def vr_query(
    query: VrQuery,
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Same capability as the per-table GET routes, with a JSON body.

    Useful when you are iterating on a query: edit one field, re-send, keep the
    body around. The GET routes remain the place to *discover* a table, since
    they carry its field reference.
    """
    try:
        spec_ = spec(query.table)
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No table named {query.table!r}. Valid tables: "
            f"{', '.join(sorted(all_specs()))}",
        ) from None

    identity = _identity_params(spec_)
    vr_filters: dict[str, str] = {}
    rejected: list[str] = []

    for field, value in query.model_dump(exclude_none=True).items():
        if field in {
            "table",
            "changed_in",
            "sort_by",
            "newest_first",
            "limit",
            "count_only",
            "filters",
        }:
            continue
        name = _ALIASES.get(field, field)
        entry = identity.get(name)
        if entry is None:
            rejected.append(name)
            continue
        vr_filters[entry[0]] = str(value)

    if rejected:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{query.table} has no {', '.join(sorted(rejected))} filter. "
            f"It accepts: {', '.join(sorted(identity)) or 'no named filters'}. "
            "Use `filters` for any other VR field name.",
        )

    # Raw passthrough, validated against the table's declared columns so a typo
    # fails here rather than returning an empty page from VR.
    unknown = [k for k in query.filters if k.split("-")[0] not in spec_.columns]
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{query.table} has no field(s) {unknown}. "
            f"Valid fields: {', '.join(sorted(spec_.columns))}",
        )
    vr_filters.update(query.filters)

    sort = None
    if query.sort_by:
        if query.sort_by not in spec_.columns:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"cannot sort by {query.sort_by!r}; {query.table} has no such field.",
            )
        sort = f"-{query.sort_by}" if query.newest_first else query.sort_by

    after = _window_start(query.changed_in)
    async with VrClient() as client:
        if not client.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
            )
        try:
            if query.count_only:
                return {
                    "table": query.table,
                    "window": query.changed_in,
                    "filters_applied": vr_filters,
                    "row_count": await client.count(query.table, changed_after=after),
                }
            page = await client.fetch_page(
                query.table,
                changed_after=after,
                sort=sort,
                filters=vr_filters or None,
            )
        except VrError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"{exc} (vendor_refused={getattr(exc, 'reached_vr', None)})",
            ) from None

    declared = set(spec_.columns)
    returned = set(page.rows[0]) if page.rows else set()
    return {
        "table": query.table,
        "window": query.changed_in,
        "filters_applied": vr_filters,
        "row_count": len(page.rows),
        "returned_rows": min(len(page.rows), query.limit),
        "has_more_pages": bool(page.next_url),
        "unknown_fields": sorted(returned - declared),
        "missing_fields": sorted(declared - returned),
        "rows": page.rows[: query.limit],
    }


class VrTableInfo(BaseModel):
    name: str
    tier: str
    fields: int
    primary_key: list[str]
    filters: list[str]
    description: str


@router.get("/tables", summary="List every queryable table and its filters")
async def list_tables(
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """What `POST /vr/query` accepts: every table, and the filters each takes.

    Read this once and the JSON body writes itself — no guessing which filter
    applies to which table.
    """
    from app.domains.vr_data.specs import _catalog  # noqa: PLC0415

    catalog = _catalog()
    tables = [
        VrTableInfo(
            name=name,
            tier=s.tier,
            fields=len(s.columns),
            primary_key=list(s.primary_key),
            filters=sorted(_identity_params(s)),
            description=catalog.get(name, {}).get("description", "")[:200],
        )
        for name, s in sorted(all_specs().items())
    ]
    return {"count": len(tables), "tables": tables}


# ``Literal`` is re-exported for the schema; keep the import meaningful.
__all__ = ["router", "VrQuery", "Literal"]
