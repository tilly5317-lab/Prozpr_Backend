"""Browse endpoints: lists of AMCs, funds and categories, and one fund's data.

The per-table routes in :mod:`.vr_tables_router` mirror Value Research exactly,
one endpoint per table. These are the questions people actually start with —
*which AMCs are there*, *find me this fund*, *show me everything about it* —
answered by composing several VR tables behind one call.

**The one thing to understand about Value Research's API:** it serves *changed
rows*, not whole tables. A request with no window returns what changed in the
last 48 hours, and the widest window the normal endpoint allows is 90 days. So
a "list all funds" call is really "list every fund whose record changed in the
window", and a fund whose row has not changed will not appear no matter how
wide you go. That is precisely why the mirror exists: sync accumulates the
universe over time, and the mirror answers completeness questions that VR's
API structurally cannot. These endpoints default to the widest window and say
so in every response, rather than quietly returning a partial list as though it
were the whole thing.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.core.dependencies import CurrentUser, get_current_user
from app.domains.vr_data.client import VrClient, VrError

router = APIRouter(prefix="/vr", tags=["VR · Browse"])

#: Widest window the paged endpoint allows. Used as the default here because
#: these are discovery calls — a 48-hour default would make the lists look
#: almost empty and read as a bug.
_WIDE = "last_90_days"
_WINDOW_DAYS = {"last_48_hours": None, "last_7_days": 7, "last_30_days": 30,
                "last_90_days": 89}
Window = Literal["last_48_hours", "last_7_days", "last_30_days", "last_90_days"]

_INCOMPLETE = (
    "Value Research serves rows that CHANGED inside the window, so this is not "
    "guaranteed to be the full universe. Sync the mirror for completeness."
)


def _window(name: str):
    from datetime import timedelta

    from app.domains.vr_data.client import today_ist

    days = _WINDOW_DAYS.get(name)
    return None if days is None else today_ist() - timedelta(days=days)


async def _fetch(
    client: VrClient,
    table: str,
    *,
    window: str,
    filters: Optional[dict[str, str]] = None,
    sort: Optional[str] = None,
    max_pages: int = 1,
) -> list[dict[str, Any]]:
    """One or more pages of a table, tolerating a table VR refuses."""
    rows: list[dict[str, Any]] = []
    url = None
    for page_no in range(max_pages):
        page = await client.fetch_page(
            table,
            changed_after=_window(window) if page_no == 0 else None,
            filters=filters,
            sort=sort,
            url=url,
        )
        rows.extend(page.rows)
        url = page.next_url
        if not url:
            break
    return rows


def _pick(row: dict[str, Any], *names: str) -> dict[str, Any]:
    return {n: row.get(n) for n in names if n in row}


async def _client() -> VrClient:
    client = VrClient()
    if not client.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "VR_API_KEY is not set"
        )
    return client


# ---------------------------------------------------------------------------
# lists
# ---------------------------------------------------------------------------


@router.get("/amcs", summary="List AMCs")
async def list_amcs(
    search: Optional[str] = Query(None, description="Match on AMC name."),
    window: Window = Query(_WIDE),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Every asset management company Value Research knows, name and id.

    The `amc_id` here is what the `amc` filter on the table endpoints takes.
    """
    async with await _client() as client:
        try:
            rows = await _fetch(client, "amcs", window=window, max_pages=2)
        except VrError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None

    out = [
        _pick(r, "amc_id", "amc_full_name", "amc_short_name", "owner_type",
              "website", "start_date")
        for r in rows
        if str(r.get("is_excluded", "")).strip().lower() not in {"y", "yes", "1", "true"}
    ]
    if search:
        needle = search.lower()
        out = [
            a
            for a in out
            if needle in str(a.get("amc_full_name", "")).lower()
            or needle in str(a.get("amc_short_name", "")).lower()
        ]
    out.sort(key=lambda a: str(a.get("amc_full_name") or ""))
    return {"count": len(out), "window": window, "note": _INCOMPLETE, "amcs": out}


@router.get("/categories", summary="List fund categories")
async def list_categories(
    window: Window = Query(_WIDE),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """SEBI and Value Research category names, with the ids to filter by."""
    async with await _client() as client:
        results: dict[str, list[dict[str, Any]]] = {}
        for table in ("sebi_categories", "fund_categories"):
            try:
                results[table] = await _fetch(client, table, window=window)
            except VrError:
                results[table] = []

    return {
        "window": window,
        "note": _INCOMPLETE,
        "sebi": sorted(
            (
                _pick(r, "sebi_category_id", "category_name",
                      "primary_category_id", "primary_category_name")
                for r in results["sebi_categories"]
            ),
            key=lambda c: str(c.get("category_name") or ""),
        ),
        "value_research": sorted(
            (
                _pick(r, "category_id", "category_name",
                      "primary_category_id", "primary_category_name")
                for r in results["fund_categories"]
            ),
            key=lambda c: str(c.get("category_name") or ""),
        ),
    }


@router.get("/funds", summary="List / search funds")
async def list_funds(
    search: Optional[str] = Query(
        None, description="Match on scheme or fund name, case-insensitive."
    ),
    amc: Optional[str] = Query(None, description="One AMC only — an amc_id."),
    category: Optional[str] = Query(
        None, description="One SEBI category only — a sebi_category_id."
    ),
    direct_only: bool = Query(False, description="Direct plans only."),
    active_only: bool = Query(True, description="Hide funds VR marks not-open."),
    window: Window = Query(_WIDE),
    limit: int = Query(100, ge=1, le=2000),
    pages: int = Query(
        2, ge=1, le=6,
        description="VR pages to walk (5000 rows each). More pages = more of "
        "the universe, and more of the hourly request budget.",
    ),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Find a fund and its `plan_id` — the id every other endpoint takes.

    `search` is applied here rather than at VR, because VR filters on exact
    field equality and has no substring match.
    """
    filters: dict[str, str] = {}
    if amc:
        filters["amc_id"] = amc
    if category:
        filters["sebi_category_id"] = category

    async with await _client() as client:
        try:
            rows = await _fetch(
                client,
                "fund_basic_details",
                window=window,
                filters=filters or None,
                max_pages=pages,
            )
        except VrError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None

    funds = [
        _pick(r, "plan_id", "scheme_name", "fund_name", "short_name", "amfi_code",
              "amc_id", "sebi_category_name", "sebi_primary_category_name",
              "vr_category_name", "status", "is_direct_plan", "is_etf_fund",
              "latest_aum", "latest_expense_ratio")
        for r in rows
    ]
    if search:
        needle = search.lower()
        funds = [
            f
            for f in funds
            if needle in str(f.get("scheme_name", "")).lower()
            or needle in str(f.get("fund_name", "")).lower()
            or needle in str(f.get("short_name", "")).lower()
        ]
    if direct_only:
        funds = [
            f
            for f in funds
            if str(f.get("is_direct_plan", "")).strip().lower()
            in {"y", "yes", "1", "true"}
        ]
    if active_only:
        funds = [
            f
            for f in funds
            if str(f.get("status", "")).strip().lower() not in {"c", "closed", "n"}
        ]
    funds.sort(key=lambda f: str(f.get("scheme_name") or ""))

    return {
        "matched": len(funds),
        "returned": min(len(funds), limit),
        "scanned_rows": len(rows),
        "window": window,
        "note": _INCOMPLETE,
        "funds": funds[:limit],
    }


# ---------------------------------------------------------------------------
# one fund, across tables
# ---------------------------------------------------------------------------

#: Section name -> (VR table, sort field or None). Each section is one request
#: against the hourly budget, which is why they are opt-in rather than all-on.
_SECTIONS: dict[str, tuple[str, Optional[str]]] = {
    "profile": ("fund_basic_details", None),
    "nav": ("nav", "-nav_date"),
    "returns": ("fund_return_latest", "-return_date"),
    "sip_returns": ("fund_sip_returns", "-as_on_date"),
    "annual_returns": ("fund_returns_annual", "-year"),
    "risk": ("stats_variables", "-as_on_date"),
    "rating": ("funds_ratings", "-rating_date"),
    "rank": ("fund_rank_latest", "-return_date"),
    "style": ("fund_stylebox_sebi", "-date"),
    "composition": ("composition", "-as_on_date"),
    "holdings": ("fund_holdings_details", "-as_on_date"),
    "sectors": ("fund_holdings_aggregate_equity", "-as_on_date"),
    "debt": ("fund_holdings_aggregate_debt", "-as_on_date"),
    "dividends": ("fund_dividends", "-div_date"),
    "transactability": ("fund_transaction_details", None),
    "isins": ("subplan_isin", None),
}

_DEFAULT_SECTIONS = ["profile", "nav", "returns", "sip_returns"]


@router.get("/fund/{plan_id}", summary="Everything about one fund")
async def fund_detail(
    plan_id: str = Path(..., description="Value Research plan_id, e.g. 16014."),
    sections: list[str] = Query(
        default=_DEFAULT_SECTIONS,
        description="Which sections to fetch. Each is one Value Research "
        "request, so ask for what you need.",
    ),
    window: Window = Query(_WIDE),
    max_rows: int = Query(20, ge=1, le=200, description="Rows per section."),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """One fund across every table that carries it, in a single call.

    Sections are fetched concurrently, but the client's shared token bucket
    still paces them against Value Research's 500/hour account limit, so this
    cannot be used to burst past it.

    A section comes back `null` when VR returned nothing for this plan inside
    the window — which, given VR serves changed rows, is common and is not an
    error. `sections_empty` lists those explicitly so an absent section is
    never mistaken for a fetch that failed.
    """
    unknown = [s for s in sections if s not in _SECTIONS]
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown section(s) {unknown}. Available: {sorted(_SECTIONS)}",
        )

    async with await _client() as client:

        async def one(section: str):
            table, sort = _SECTIONS[section]
            try:
                rows = await _fetch(
                    client,
                    table,
                    window=window,
                    filters={"plan_id": plan_id},
                    sort=sort,
                )
                return section, {"table": table, "rows": rows[:max_rows]}, None
            except VrError as exc:
                return section, None, str(exc)

        gathered = await asyncio.gather(*(one(s) for s in sections))

    data: dict[str, Any] = {}
    empty: list[str] = []
    failed: dict[str, str] = {}
    for section, payload, error in gathered:
        if error:
            failed[section] = error
            continue
        if not payload["rows"]:
            data[section] = None
            empty.append(section)
        else:
            data[section] = payload

    return {
        "plan_id": plan_id,
        "window": window,
        "sections_requested": sections,
        "sections_empty": empty,
        "sections_failed": failed,
        "note": (
            "An empty section means VR reported no change for this fund inside "
            "the window; it does not mean the data does not exist. " + _INCOMPLETE
        ),
        **data,
    }
