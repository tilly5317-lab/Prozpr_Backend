"""HTTP client for the Value Research data API.

One endpoint per table, ``API_KEY`` as a **request header**, JSON out. The
contract details that bite are encoded here rather than left to call sites:

* **``output`` defaults to ``count``.** An endpoint that looks silent is almost
  always just missing ``output=data``, so we always send it.
* **``changed-after`` may not exceed 90 days** on the normal path; anything
  older needs the async ``bulk-request`` route. :meth:`VrClient.fetch_page`
  refuses a stale window up front instead of letting VR reject it.
* **``changed-before`` is only valid alongside ``changed-after``** and caps the
  result at 7 days.
* **Omitting both returns the last 48 hours**, which is a fine default for a
  daily job but a silent data loss for a weekly one — so the sync always sends
  an explicit window.
* **Rate limit is 500 requests/hour**, shared across every table. A single
  in-process token bucket guards it; a burnt hour stalls every job, not one.
* **The host sits behind Cloudflare**, which answers a rejected request with an
  HTML challenge page rather than a JSON error. A 403 with an HTML body never
  reached VR at all (bad or absent key, non-whitelisted IP) and is a
  configuration bug; a 403 with a JSON body is VR refusing *that table* for
  this key, which is a contract question. :class:`VrAccessError` distinguishes
  them because the two have completely different fixes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: VR's stated ceiling for ``changed-after`` on the paged endpoints.
MAX_INCREMENTAL_WINDOW_DAYS = 90
#: Rows VR returns per page before handing back a ``links.next``.
PAGE_SIZE = 5000
#: Bulk generation attempts VR allows per table per calendar day.
BULK_REQUESTS_PER_TABLE_PER_DAY = 2

IST = timezone(timedelta(hours=5, minutes=30))


class VrError(RuntimeError):
    """Any failure talking to Value Research."""


class VrNotConfigured(VrError):
    """No API key — the integration is inert rather than broken."""


class VrAccessError(VrError):
    """A 403. ``reached_vr`` is the whole diagnosis.

    ``reached_vr=False`` — Cloudflare blocked it. The key is missing/wrong or
    this egress IP is not whitelisted. Ours is a single EC2 box, so a new NAT
    or a rebuilt instance is the usual cause.

    ``reached_vr=True`` — VR itself refused this table for this key, i.e. the
    table is outside the contract. Not retryable; take it to the vendor.
    """

    def __init__(self, message: str, *, reached_vr: bool, table: str) -> None:
        super().__init__(message)
        self.reached_vr = reached_vr
        self.table = table


class VrRateLimited(VrError):
    """Local budget exhausted, or VR returned 429."""


class VrDataNotReady(VrError):
    """A bulk extract is still generating (``DATA_NOT_READY``). Poll later."""


@dataclass
class VrPage:
    """One page of rows plus the cursor to the next, if any."""

    rows: list[dict[str, Any]]
    next_url: Optional[str]
    raw_links: dict[str, Any]


class _TokenBucket:
    """Requests/hour ceiling, shared by every table in this process.

    Deliberately not per-table: VR's 500/hour is an account limit, so a holdings
    backfill that ignored it would starve the NAV job.
    """

    def __init__(self, capacity: int, per_seconds: float = 3600.0) -> None:
        self._capacity = capacity
        self._per_seconds = per_seconds
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self, *, wait: bool) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens
                    + (now - self._updated) * self._capacity / self._per_seconds,
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) * self._per_seconds / self._capacity
                if not wait:
                    raise VrRateLimited(
                        f"VR request budget exhausted; next token in {deficit:.0f}s"
                    )
                await asyncio.sleep(min(deficit, 30.0))

    @property
    def available(self) -> int:
        now = time.monotonic()
        tokens = min(
            self._capacity,
            self._tokens + (now - self._updated) * self._capacity / self._per_seconds,
        )
        return int(tokens)


def format_window(value: date | datetime) -> str:
    """VR window format — ``YYYY-MM-DD`` or ``YYYY-MM-DD-HH-MM``, IST."""
    if isinstance(value, datetime):
        ist = value.astimezone(IST) if value.tzinfo else value
        return ist.strftime("%Y-%m-%d-%H-%M")
    return value.strftime("%Y-%m-%d")


def today_ist() -> date:
    return datetime.now(IST).date()


class VrClient:
    """Async client. One per process; share it, do not build one per call."""

    _bucket: Optional[_TokenBucket] = None

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.get_vr_base_url()).rstrip("/")
        self._api_key = api_key if api_key is not None else settings.get_vr_api_key()
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        if VrClient._bucket is None:
            VrClient._bucket = _TokenBucket(settings.get_vr_rate_limit_per_hour())

    # -- lifecycle ---------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> str:
        if not self._api_key:
            raise VrNotConfigured(
                "VR_API_KEY is not set — Value Research sync is disabled."
            )
        return self._api_key

    async def __aenter__(self) -> "VrClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"API_KEY": self._require_key(), "Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise VrError("VrClient used outside its async context manager")
        return self._client

    @property
    def budget_remaining(self) -> int:
        assert VrClient._bucket is not None
        return VrClient._bucket.available

    # -- requests ----------------------------------------------------------

    async def _get(
        self, url: str, params: Optional[dict[str, Any]], *, table: str
    ) -> dict[str, Any]:
        assert VrClient._bucket is not None
        await VrClient._bucket.take(wait=True)
        try:
            response = await self._http().get(url, params=params)
        except httpx.HTTPError as exc:
            raise VrError(f"{table}: transport error calling VR: {exc}") from exc

        if response.status_code == 403:
            body = response.text or ""
            content_type = response.headers.get("content-type", "")
            reached_vr = "json" in content_type.lower() and not body.lstrip().startswith(
                "<"
            )
            raise VrAccessError(
                (
                    f"{table}: VR refused this table for this key ({body[:200]})"
                    if reached_vr
                    else (
                        f"{table}: blocked before reaching VR (Cloudflare). "
                        "Check VR_API_KEY and that this egress IP is whitelisted."
                    )
                ),
                reached_vr=reached_vr,
                table=table,
            )
        if response.status_code == 429:
            raise VrRateLimited(f"{table}: VR returned 429")
        if response.status_code >= 400:
            raise VrError(
                f"{table}: VR returned {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise VrError(f"{table}: VR returned non-JSON body") from exc
        if not isinstance(payload, dict):
            return {"data": payload, "links": {}}
        return payload

    async def fetch_page(
        self,
        table: str,
        *,
        changed_after: Optional[date | datetime] = None,
        changed_before: Optional[date | datetime] = None,
        filters: Optional[dict[str, str]] = None,
        sort: Optional[str] = None,
        url: Optional[str] = None,
    ) -> VrPage:
        """One page. Pass ``url`` (a ``links.next``) to continue a walk.

        Rejects a ``changed-after`` older than 90 days locally — VR would
        refuse it anyway, and spending a token to be told so is wasteful when
        the real answer is the bulk route.
        """
        self._require_key()
        if url:
            payload = await self._get(url, None, table=table)
        else:
            if changed_after is not None:
                as_date = (
                    changed_after.date()
                    if isinstance(changed_after, datetime)
                    else changed_after
                )
                age = (today_ist() - as_date).days
                if age > MAX_INCREMENTAL_WINDOW_DAYS:
                    raise VrError(
                        f"{table}: changed-after {as_date} is {age} days old; the "
                        f"paged endpoint allows {MAX_INCREMENTAL_WINDOW_DAYS}. "
                        "Use request_bulk_extract() for a historical backfill."
                    )
            if changed_before is not None and changed_after is None:
                raise VrError(
                    f"{table}: changed-before is only valid with changed-after"
                )

            params: dict[str, Any] = {"output": "data"}
            if changed_after is not None:
                params["changed-after"] = format_window(changed_after)
            if changed_before is not None:
                params["changed-before"] = format_window(changed_before)
            if sort:
                params["SORT"] = sort
            if filters:
                params.update(filters)
            payload = await self._get(
                f"{self.base_url}/v1/{table}", params, table=table
            )

        data = payload.get("data")
        if data is None:
            data = payload.get("records") or []
        if isinstance(data, dict):
            data = [data]
        links = payload.get("links") or {}
        next_url = links.get("next") if isinstance(links, dict) else None
        # VR echoes the current page as `next` on the last page in some
        # responses; treat a self-referential cursor as the end of the walk.
        if next_url and url and next_url == url:
            next_url = None
        return VrPage(rows=list(data), next_url=next_url, raw_links=links or {})

    async def count(
        self, table: str, *, changed_after: Optional[date | datetime] = None
    ) -> Optional[int]:
        """Row count for a window — ``output=count``, VR's default mode.

        Cheap enough to run before a big page walk, which is how the ops
        endpoint answers "how much is waiting" without spending the budget on
        it.
        """
        self._require_key()
        params: dict[str, Any] = {"output": "count"}
        if changed_after is not None:
            params["changed-after"] = format_window(changed_after)
        payload = await self._get(f"{self.base_url}/v1/{table}", params, table=table)
        for key in ("count", "total", "records", "data"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, dict) and isinstance(value.get("count"), int):
                return value["count"]
        return None

    async def describe(self) -> dict[str, Any]:
        """``GET /describe`` — the table list **this key** is entitled to.

        The cheapest and most authoritative answer to "which tables do we
        actually get": one request, no window, no rows, and it reflects the
        contract rather than the published catalogue. Run this before arguing
        table-by-table with the vendor.

        ``?format=html`` returns the same thing as a page; we take the JSON.
        """
        self._require_key()
        return await self._get(f"{self.base_url}/describe", None, table="describe")

    async def request_bulk_extract(
        self, table: str, *, changed_after: Optional[date | datetime] = None
    ) -> dict[str, Any]:
        """Kick off an async bulk extract for data older than 90 days.

        Returns VR's two URLs and **no rows** — the paged one answers
        ``DATA_NOT_READY`` until generation finishes, the other is a zip of
        CSVs. Capped at two per table per calendar day with 24h validity, and
        the caller (:mod:`.services.backfill_service`) must have already
        decremented ``vr.bulk_budget`` — this method does not police the budget
        itself, because the guard has to be transactional with the DB.
        """
        self._require_key()
        params: dict[str, Any] = {}
        if changed_after is not None:
            params["changed-after"] = format_window(changed_after)
        return await self._get(
            f"{self.base_url}/bulk-request/v1/{table}", params or None, table=table
        )

    async def poll_bulk(self, url: str, *, table: str) -> VrPage:
        """Read a bulk extract's paged URL, raising until VR has built it."""
        payload = await self._get(url, None, table=table)
        status = str(payload.get("status") or payload.get("message") or "")
        if "DATA_NOT_READY" in status.upper():
            raise VrDataNotReady(f"{table}: extract still generating")
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = [data]
        links = payload.get("links") or {}
        return VrPage(
            rows=list(data),
            next_url=links.get("next") if isinstance(links, dict) else None,
            raw_links=links or {},
        )
