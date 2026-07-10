# NSE Nifty 50 TRI Scraper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch, store, and auto-refresh the NSE Nifty 50 Total Return Index (TRI) history so a later benchmarking module can value a hypothetical "same money in Nifty 50" position on any date.

**Architecture:** Mirror the existing mfapi NAV machinery in `app/domains/mutual_funds/`: a pure-HTTP fetcher (`niftyindices_fetcher.py`) ↔ a DB/orchestration service (`index_tri_service.py`) ↔ an independent APScheduler (`index_tri_scheduler.py`) wired into `app/core/lifespan.py`. Data lands in a new `index_tri_history` table.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, httpx, APScheduler, PostgreSQL (prod) / sqlite+aiosqlite (tests), pytest + pytest-asyncio (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-06-05-nse-nifty50-tri-scraper-design.md`

---

## File Structure

**Create:**
- `app/domains/mutual_funds/models/index_tri_history.py` — ORM table `index_tri_history`.
- `app/domains/mutual_funds/schemas/index_tri.py` — pydantic create/response models.
- `app/domains/mutual_funds/services/niftyindices_fetcher.py` — HTTP client (retry + 2-year chunking + parse).
- `app/domains/mutual_funds/services/index_tri_service.py` — bulk insert, backfill, incremental refresh, read accessor, scheduled-job fn.
- `app/domains/mutual_funds/services/index_tri_scheduler.py` — independent AsyncIOScheduler.
- `app/domains/mutual_funds/tests/__init__.py` — make tests a package.
- `app/domains/mutual_funds/tests/test_niftyindices_fetcher.py` — fetcher unit tests (mock transport).
- `app/domains/mutual_funds/tests/test_index_tri_service.py` — service unit tests (sqlite + monkeypatch).
- `alembic/versions/e1a2b3c4d5e6_add_index_tri_history.py` — migration.

**Modify:**
- `app/all_models.py` — register the new model with `Base.metadata`.
- `app/domains/mutual_funds/models/__init__.py` — export `IndexTriHistory`.
- `app/domains/mutual_funds/schemas/__init__.py` — export new schemas.
- `app/core/config.py` — add `index_tri_scheduler_enabled()`.
- `app/core/lifespan.py` — start/stop the new scheduler.

---

## Task 1: Model + registration + migration

**Files:**
- Create: `app/domains/mutual_funds/models/index_tri_history.py`
- Modify: `app/domains/mutual_funds/models/__init__.py`
- Modify: `app/all_models.py:27-30`
- Create: `app/domains/mutual_funds/tests/__init__.py`
- Create: `app/domains/mutual_funds/tests/test_index_tri_service.py` (model import test only in this task)
- Create: `alembic/versions/e1a2b3c4d5e6_add_index_tri_history.py`

- [ ] **Step 1: Write the failing test** — create `app/domains/mutual_funds/tests/__init__.py` (empty) and `app/domains/mutual_funds/tests/test_index_tri_service.py`:

```python
"""Tests for the Nifty 50 TRI scraper (model + service)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers every ORM with Base.metadata
from app.core.database import Base


def test_index_tri_history_is_registered():
    assert "index_tri_history" in Base.metadata.tables
    cols = Base.metadata.tables["index_tri_history"].columns.keys()
    assert {"id", "index_name", "tri_date", "tri_value", "ntr_value", "created_at"} <= set(cols)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py::test_index_tri_history_is_registered -v`
Expected: FAIL — `"index_tri_history" not in Base.metadata.tables` (KeyError/AssertionError).

- [ ] **Step 3: Write the model** — create `app/domains/mutual_funds/models/index_tri_history.py`:

```python
"""SQLAlchemy ORM model — `index_tri_history.py`.

Daily NSE index Total Return Index (TRI) history. Standalone (no FK): index
data is independent of the fund universe. Stores both Gross TRI (``tri_value``,
the benchmark) and Net TRI (``ntr_value``). Multi-index ready via ``index_name``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IndexTriHistory(Base):
    """Daily TRI feed for an NSE index (e.g. NIFTY 50)."""

    __tablename__ = "index_tri_history"
    __table_args__ = (
        UniqueConstraint("index_name", "tri_date", name="uq_index_tri_name_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    index_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tri_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tri_value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    ntr_value: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Export from models package** — in `app/domains/mutual_funds/models/__init__.py`, add the import (after the `mf_nav_history` import line) and the `__all__` entry:

```python
from app.domains.mutual_funds.models.index_tri_history import IndexTriHistory
```

Add `"IndexTriHistory",` to the `__all__` list.

- [ ] **Step 5: Register in `app/all_models.py`** — extend the existing mutual_funds import block at `app/all_models.py:27-30` so the module is imported (this is what registers it with `Base.metadata`). Add `index_tri_history` to the module list:

```python
from app.domains.mutual_funds.models import (  # noqa: F401
    enums,
    fund, mf_aa_import, mf_fund_metadata, mf_fund_rating, mf_nav_history,
    index_tri_history,
    # ... keep the remaining existing entries unchanged
)
```

(Match the exact existing multiline form; just add `index_tri_history,` to it.)

- [ ] **Step 6: Run test to verify it passes**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py::test_index_tri_history_is_registered -v`
Expected: PASS.

- [ ] **Step 7: Write the Alembic migration** — create `alembic/versions/e1a2b3c4d5e6_add_index_tri_history.py`. Set `down_revision` to the current head (run `cd ailax/Prozpr_Backend && python -m alembic heads` to confirm; at time of writing it is `d8e0f1a2b3c4`):

```python
"""Add index_tri_history table.

Revision ID: e1a2b3c4d5e6
Revises: d8e0f1a2b3c4
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, None] = "d8e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "index_tri_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("index_name", sa.String(length=50), nullable=False),
        sa.Column("tri_date", sa.Date(), nullable=False),
        sa.Column("tri_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("ntr_value", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("index_name", "tri_date", name="uq_index_tri_name_date"),
    )
    op.create_index("ix_index_tri_history_index_name", "index_tri_history", ["index_name"])
    op.create_index("ix_index_tri_history_tri_date", "index_tri_history", ["tri_date"])


def downgrade() -> None:
    op.drop_index("ix_index_tri_history_tri_date", table_name="index_tri_history")
    op.drop_index("ix_index_tri_history_index_name", table_name="index_tri_history")
    op.drop_table("index_tri_history")
```

- [ ] **Step 8: Verify the migration renders** (offline SQL, no DB needed)

Run: `cd ailax/Prozpr_Backend && python -m alembic upgrade d8e0f1a2b3c4:e1a2b3c4d5e6 --sql`
Expected: prints a `CREATE TABLE index_tri_history (...)` statement with the unique constraint, no errors.

- [ ] **Step 9: Commit**

```bash
git add app/domains/mutual_funds/models/index_tri_history.py \
        app/domains/mutual_funds/models/__init__.py \
        app/all_models.py \
        app/domains/mutual_funds/tests/__init__.py \
        app/domains/mutual_funds/tests/test_index_tri_service.py \
        alembic/versions/e1a2b3c4d5e6_add_index_tri_history.py
git commit -m "feat(mf): add index_tri_history model + migration"
```

---

## Task 2: Schemas

**Files:**
- Create: `app/domains/mutual_funds/schemas/index_tri.py`
- Modify: `app/domains/mutual_funds/schemas/__init__.py:34` (import) and `__all__`

- [ ] **Step 1: Write the failing test** — append to `app/domains/mutual_funds/tests/test_index_tri_service.py`:

```python
def test_index_tri_response_schema_from_attributes():
    from app.domains.mutual_funds.schemas import IndexTriHistoryResponse

    obj = type("Row", (), {})()
    obj.id = uuid.uuid4()
    obj.index_name = "NIFTY 50"
    obj.tri_date = date(2024, 1, 31)
    obj.tri_value = Decimal("31939.59")
    obj.ntr_value = Decimal("28933.54")
    obj.created_at = __import__("datetime").datetime.now()

    resp = IndexTriHistoryResponse.model_validate(obj)
    assert resp.index_name == "NIFTY 50"
    assert float(resp.tri_value) == pytest.approx(31939.59)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py::test_index_tri_response_schema_from_attributes -v`
Expected: FAIL — `ImportError: cannot import name 'IndexTriHistoryResponse'`.

- [ ] **Step 3: Write the schemas** — create `app/domains/mutual_funds/schemas/index_tri.py`:

```python
"""NSE index TRI rows."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class IndexTriHistoryCreate(BaseModel):
    index_name: str = Field(..., max_length=50)
    tri_date: date
    tri_value: float = Field(..., gt=0)
    ntr_value: Optional[float] = Field(None, gt=0)


class IndexTriHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    index_name: str
    tri_date: date
    tri_value: float
    ntr_value: Optional[float]
    created_at: datetime
```

- [ ] **Step 4: Export from schemas package** — in `app/domains/mutual_funds/schemas/__init__.py`, add near the nav_history import (line ~34):

```python
from app.domains.mutual_funds.schemas.index_tri import IndexTriHistoryCreate, IndexTriHistoryResponse
```

Add `"IndexTriHistoryCreate",` and `"IndexTriHistoryResponse",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py::test_index_tri_response_schema_from_attributes -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/domains/mutual_funds/schemas/index_tri.py app/domains/mutual_funds/schemas/__init__.py
git commit -m "feat(mf): add index TRI pydantic schemas"
```

---

## Task 3: Fetcher (HTTP + parse + chunking)

**Files:**
- Create: `app/domains/mutual_funds/services/niftyindices_fetcher.py`
- Create: `app/domains/mutual_funds/tests/test_niftyindices_fetcher.py`

- [ ] **Step 1: Write the failing tests** — create `app/domains/mutual_funds/tests/test_niftyindices_fetcher.py`:

```python
"""Tests for the niftyindices TRI HTTP fetcher (no real network)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.domains.mutual_funds.services import niftyindices_fetcher as f


def test_iter_date_windows_splits_into_two_year_chunks():
    windows = list(f.iter_date_windows(date(2016, 1, 1), date(2021, 6, 30), window_years=2))
    assert windows[0][0] == date(2016, 1, 1)
    assert windows[-1][1] == date(2021, 6, 30)
    # contiguous, non-overlapping, each <= ~2 years
    for (_, end), (nxt_start, _) in zip(windows, windows[1:]):
        assert (nxt_start - end).days == 1
    assert all(start <= end for start, end in windows)


def test_parse_record_maps_fields():
    row = f._parse_record(
        {"Date": "31 Jan 2024", "TotalReturnsIndex": "31939.59", "NTR_Value": "28933.54"}
    )
    assert row.tri_date == date(2024, 1, 31)
    assert row.tri_value == Decimal("31939.59")
    assert row.ntr_value == Decimal("28933.54")


@pytest.mark.asyncio
async def test_fetch_tri_parses_response_and_sorts_ascending():
    payload = {
        "d": json.dumps(
            [
                {"Date": "02 Jan 2024", "TotalReturnsIndex": "31837.56", "NTR_Value": "28844.36"},
                {"Date": "01 Jan 2024", "TotalReturnsIndex": "31949.36", "NTR_Value": "28945.65"},
            ]
        )
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        rows = await f.fetch_tri(client, "NIFTY 50", date(2024, 1, 1), date(2024, 1, 2))

    assert [r.tri_date for r in rows] == [date(2024, 1, 1), date(2024, 1, 2)]
    assert rows[0].tri_value == Decimal("31949.36")


@pytest.mark.asyncio
async def test_fetch_tri_raises_after_retries_on_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(f.NiftyTriFetchError):
            await f.fetch_tri(
                client, "NIFTY 50", date(2024, 1, 1), date(2024, 1, 2), max_retries=2, backoff_base=0
            )


@pytest.mark.asyncio
async def test_fetch_tri_chunked_concatenates_and_dedupes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "d": json.dumps(
                    [{"Date": "01 Jan 2024", "TotalReturnsIndex": "100.0", "NTR_Value": "90.0"}]
                )
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        rows = await f.fetch_tri_chunked(
            client, "NIFTY 50", date(2016, 1, 1), date(2021, 1, 1), window_years=2
        )
    # same canned date returned for every window → deduped to one row
    assert len(rows) == 1
    assert rows[0].tri_date == date(2024, 1, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_niftyindices_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (module/functions don't exist yet).

- [ ] **Step 3: Write the fetcher** — create `app/domains/mutual_funds/services/niftyindices_fetcher.py`:

```python
"""Async client for the niftyindices.com Total Return Index (TRI) feed.

One operation: POST ``getTotalReturnIndexString`` returns daily TRI rows for an
index over a date range. Long ranges time out, so callers use ``fetch_tri_chunked``
to split into <=2-year windows. Mirrors the retry-with-backoff posture of
``mfapi_fetcher.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

NIFTY_TRI_URL = "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
NIFTY_TRI_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.niftyindices.com/reports/historical-data",
    "X-Requested-With": "XMLHttpRequest",
}
NIFTY_TRI_TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)
NIFTY_TRI_MAX_RETRIES = 3
DEFAULT_INDEX_NAME = "NIFTY 50"
NIFTY_TRI_EARLIEST = date(1999, 6, 30)  # earliest TRI date published by NSE


class NiftyTriFetchError(RuntimeError):
    """Raised when the niftyindices TRI feed cannot be retrieved or parsed."""


@dataclass(slots=True)
class TriRow:
    tri_date: date
    tri_value: Decimal
    ntr_value: Optional[Decimal]


def _fmt(d: date) -> str:
    """niftyindices expects 'DD-Mon-YYYY' (e.g. 04-Jun-2026)."""
    return d.strftime("%d-%b-%Y")


def _parse_record(rec: dict) -> Optional[TriRow]:
    """Map one response record → TriRow; return None if malformed."""
    try:
        d = datetime.strptime(str(rec["Date"]).strip(), "%d %b %Y").date()
        tri = Decimal(str(rec["TotalReturnsIndex"]).strip())
    except (KeyError, ValueError, InvalidOperation, TypeError):
        return None
    ntr_raw = rec.get("NTR_Value")
    try:
        ntr = Decimal(str(ntr_raw).strip()) if ntr_raw not in (None, "") else None
    except (InvalidOperation, ValueError, TypeError):
        ntr = None
    return TriRow(tri_date=d, tri_value=tri, ntr_value=ntr)


def iter_date_windows(start: date, end: date, window_years: int = 2):
    """Yield contiguous, non-overlapping (start, end) windows of <= window_years."""
    cur = start
    while cur <= end:
        win_end = min(date(cur.year + window_years, cur.month, cur.day) - timedelta(days=1), end)
        yield cur, win_end
        cur = win_end + timedelta(days=1)


async def fetch_tri(
    client: httpx.AsyncClient,
    index_name: str,
    start: date,
    end: date,
    *,
    max_retries: int = NIFTY_TRI_MAX_RETRIES,
    backoff_base: float = 1.0,
) -> list[TriRow]:
    """Fetch TRI rows for [start, end], parsed and sorted ascending by date."""
    cinfo = (
        "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}"
        % (index_name, _fmt(start), _fmt(end), index_name)
    )
    body = {"cinfo": cinfo}
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.post(
                NIFTY_TRI_URL, json=body, headers=NIFTY_TRI_HEADERS, timeout=NIFTY_TRI_TIMEOUT
            )
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"transient {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            outer = resp.json()
            raw = json.loads(outer["d"])  # 'd' is a JSON-encoded string
            rows = [r for r in (_parse_record(rec) for rec in raw) if r is not None]
            rows.sort(key=lambda r: r.tri_date)
            return rows
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
    raise NiftyTriFetchError(
        f"niftyindices TRI fetch failed for {index_name} {start}..{end}: {last_exc}"
    ) from last_exc


async def fetch_tri_chunked(
    client: httpx.AsyncClient,
    index_name: str,
    start: date,
    end: date,
    *,
    window_years: int = 2,
) -> list[TriRow]:
    """Fetch [start, end] in <=window_years windows; concat + dedupe by date, ascending."""
    by_date: dict[date, TriRow] = {}
    for win_start, win_end in iter_date_windows(start, end, window_years=window_years):
        for row in await fetch_tri(client, index_name, win_start, win_end):
            by_date[row.tri_date] = row
    return [by_date[d] for d in sorted(by_date)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_niftyindices_fetcher.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/mutual_funds/services/niftyindices_fetcher.py \
        app/domains/mutual_funds/tests/test_niftyindices_fetcher.py
git commit -m "feat(mf): add niftyindices TRI fetcher with retry + chunking"
```

---

## Task 4: Service (bulk insert, backfill, incremental, accessor, job fn)

**Files:**
- Create: `app/domains/mutual_funds/services/index_tri_service.py`
- Modify: `app/domains/mutual_funds/tests/test_index_tri_service.py` (add service tests)

- [ ] **Step 1: Write the failing tests** — append to `app/domains/mutual_funds/tests/test_index_tri_service.py`:

```python
@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def test_incremental_start_from_high_water_mark():
    from app.domains.mutual_funds.services import index_tri_service as svc
    from app.domains.mutual_funds.services.niftyindices_fetcher import NIFTY_TRI_EARLIEST

    assert svc._incremental_start(None) == NIFTY_TRI_EARLIEST
    assert svc._incremental_start(date(2024, 1, 31)) == date(2024, 2, 1)


@pytest.mark.asyncio
async def test_get_tri_on_or_before_returns_nearest_prior(db_session):
    from app.domains.mutual_funds.models import IndexTriHistory
    from app.domains.mutual_funds.services import index_tri_service as svc

    for d, v in [(date(2024, 1, 5), "100"), (date(2024, 1, 8), "110")]:
        db_session.add(
            IndexTriHistory(index_name="NIFTY 50", tri_date=d, tri_value=Decimal(v), ntr_value=None)
        )
    await db_session.flush()

    # Sunday 2024-01-07 → nearest prior trading day is Friday 2024-01-05
    row = await svc.get_tri_on_or_before(db_session, "NIFTY 50", date(2024, 1, 7))
    assert row is not None and row.tri_date == date(2024, 1, 5)

    # before any data → None
    assert await svc.get_tri_on_or_before(db_session, "NIFTY 50", date(2023, 1, 1)) is None


@pytest.mark.asyncio
async def test_refresh_incremental_fetches_from_day_after_high_water_mark(db_session, monkeypatch):
    from app.domains.mutual_funds.models import IndexTriHistory
    from app.domains.mutual_funds.services import index_tri_service as svc
    from app.domains.mutual_funds.services.niftyindices_fetcher import TriRow

    db_session.add(
        IndexTriHistory(
            index_name="NIFTY 50", tri_date=date(2024, 1, 31), tri_value=Decimal("100"), ntr_value=None
        )
    )
    await db_session.flush()

    captured = {}

    async def fake_fetch_chunked(client, index_name, start, end, **kw):
        captured["start"] = start
        return [TriRow(tri_date=date(2024, 2, 1), tri_value=Decimal("101"), ntr_value=None)]

    async def fake_bulk_insert(db, index_name, rows):
        return len(list(rows))

    monkeypatch.setattr(svc, "fetch_tri_chunked", fake_fetch_chunked)
    monkeypatch.setattr(svc, "bulk_insert_tri_rows", fake_bulk_insert)

    inserted = await svc.refresh_incremental(db_session, "NIFTY 50")
    assert captured["start"] == date(2024, 2, 1)  # day after high-water mark
    assert inserted == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py -v`
Expected: FAIL — `ModuleNotFoundError: ...index_tri_service`.

- [ ] **Step 3: Write the service** — create `app/domains/mutual_funds/services/index_tri_service.py`:

```python
"""Backfill, incremental refresh, and read access for ``index_tri_history``.

Mirrors ``nav_history_service`` (pg upsert + chunking) and hosts the scheduled
job function the independent TRI scheduler calls. Caller manages the
transaction for write helpers (no commit here) except ``run_tri_refresh_job``,
which owns its own session + advisory lock.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Iterable, Optional

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models import IndexTriHistory
from app.domains.mutual_funds.services.niftyindices_fetcher import (
    DEFAULT_INDEX_NAME,
    NIFTY_TRI_EARLIEST,
    NIFTY_TRI_TIMEOUT,
    TriRow,
    fetch_tri_chunked,
)

logger = logging.getLogger(__name__)

INDEX_TRI_LOCK_KEY = 7421101  # distinct from MFAPI_LOCK_KEY (7421100)
_BULK_CHUNK_SIZE = 500


async def bulk_insert_tri_rows(
    db: AsyncSession, index_name: str, rows: Iterable[TriRow]
) -> int:
    """Insert rows with ON CONFLICT (index_name, tri_date) DO NOTHING. Idempotent."""
    payload = [
        {
            "index_name": index_name,
            "tri_date": r.tri_date,
            "tri_value": r.tri_value,
            "ntr_value": r.ntr_value,
        }
        for r in rows
    ]
    if not payload:
        return 0
    total = 0
    for start in range(0, len(payload), _BULK_CHUNK_SIZE):
        chunk = payload[start : start + _BULK_CHUNK_SIZE]
        stmt = pg_insert(IndexTriHistory).values(chunk)
        stmt = stmt.on_conflict_do_nothing(index_elements=["index_name", "tri_date"])
        result = await db.execute(stmt)
        total += int(result.rowcount or 0)
    return total


async def _max_tri_date(db: AsyncSession, index_name: str) -> Optional[date]:
    return (
        await db.execute(
            select(func.max(IndexTriHistory.tri_date)).where(
                IndexTriHistory.index_name == index_name
            )
        )
    ).scalar()


def _incremental_start(high_water_mark: Optional[date]) -> date:
    """First date to fetch: day after the latest stored row, or earliest if empty."""
    if high_water_mark is None:
        return NIFTY_TRI_EARLIEST
    return high_water_mark + timedelta(days=1)


async def backfill_full_history(
    db: AsyncSession, index_name: str = DEFAULT_INDEX_NAME
) -> int:
    """Fetch full available history (earliest → today) and bulk insert. Re-run safe."""
    today = date.today()
    async with httpx.AsyncClient(timeout=NIFTY_TRI_TIMEOUT) as client:
        rows = await fetch_tri_chunked(client, index_name, NIFTY_TRI_EARLIEST, today)
    inserted = await bulk_insert_tri_rows(db, index_name, rows)
    logger.info("TRI backfill %s: fetched=%d inserted=%d", index_name, len(rows), inserted)
    return inserted


async def refresh_incremental(
    db: AsyncSession, index_name: str = DEFAULT_INDEX_NAME
) -> int:
    """Fetch only rows newer than the stored high-water mark and bulk insert."""
    start = _incremental_start(await _max_tri_date(db, index_name))
    today = date.today()
    if start > today:
        logger.info("TRI refresh %s: up to date (start %s > today)", index_name, start)
        return 0
    async with httpx.AsyncClient(timeout=NIFTY_TRI_TIMEOUT) as client:
        rows = await fetch_tri_chunked(client, index_name, start, today)
    inserted = await bulk_insert_tri_rows(db, index_name, rows)
    logger.info(
        "TRI refresh %s: from=%s fetched=%d inserted=%d", index_name, start, len(rows), inserted
    )
    return inserted


async def get_tri_on_or_before(
    db: AsyncSession, index_name: str, on: date
) -> Optional[IndexTriHistory]:
    """Nearest trading-day TRI row with tri_date <= ``on`` (for benchmark valuation)."""
    return (
        await db.execute(
            select(IndexTriHistory)
            .where(IndexTriHistory.index_name == index_name, IndexTriHistory.tri_date <= on)
            .order_by(IndexTriHistory.tri_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def run_tri_refresh_job(index_name: str = DEFAULT_INDEX_NAME) -> None:
    """Scheduled entry: own session + advisory lock, then incremental refresh.

    First run against an empty table naturally backfills full history (the
    high-water mark is None → start = earliest date).
    """
    from app.core.database import _get_session_factory

    t0 = time.monotonic()
    factory = _get_session_factory()
    try:
        async with factory() as db:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": INDEX_TRI_LOCK_KEY}
                )
            ).scalar()
            if not got_lock:
                logger.info("TRI job: lock held by another worker; skipping")
                return
            try:
                inserted = await refresh_incremental(db, index_name)
                await db.commit()
                logger.info(
                    "TRI job done in %.1fs: inserted=%d", time.monotonic() - t0, inserted
                )
            except Exception:
                await db.rollback()
                logger.exception("TRI job crashed after %.1fs", time.monotonic() - t0)
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": INDEX_TRI_LOCK_KEY}
                    )
                except SQLAlchemyError:
                    logger.warning("TRI job: failed to release advisory lock", exc_info=True)
    except SQLAlchemyError:
        logger.warning("TRI job: database unavailable; will retry next schedule", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py -v`
Expected: PASS (all service + model + schema tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/mutual_funds/services/index_tri_service.py \
        app/domains/mutual_funds/tests/test_index_tri_service.py
git commit -m "feat(mf): add index TRI service (backfill, incremental, accessor, job)"
```

---

## Task 5: Independent scheduler + config flag + lifespan wiring

**Files:**
- Create: `app/domains/mutual_funds/services/index_tri_scheduler.py`
- Modify: `app/core/config.py` (add `index_tri_scheduler_enabled` after `mfapi_scheduler_enabled`, ~line 329)
- Modify: `app/core/lifespan.py` (imports, `_start_schedulers`, `_shutdown`)
- Modify: `app/domains/mutual_funds/tests/test_index_tri_service.py` (scheduler + config tests)

- [ ] **Step 1: Write the failing tests** — append to `app/domains/mutual_funds/tests/test_index_tri_service.py`:

```python
def test_index_tri_scheduler_enabled_default_and_off(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("INDEX_TRI_SCHEDULER_ENABLED", raising=False)
    assert Settings.index_tri_scheduler_enabled() is True
    monkeypatch.setenv("INDEX_TRI_SCHEDULER_ENABLED", "false")
    assert Settings.index_tri_scheduler_enabled() is False


def test_tri_scheduler_registers_one_job_then_shuts_down():
    from app.domains.mutual_funds.services import index_tri_scheduler as s

    sched = s.start_tri_scheduler()
    try:
        assert sched is not None
        jobs = sched.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "index_tri_daily_refresh"
    finally:
        # synchronous shutdown for the test
        sched.shutdown(wait=False)
        s._tri_scheduler = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py -k "tri_scheduler or scheduler_enabled" -v`
Expected: FAIL — missing `index_tri_scheduler` module and `Settings.index_tri_scheduler_enabled`.

- [ ] **Step 3: Add the config flag** — in `app/core/config.py`, directly after the `mfapi_scheduler_enabled` staticmethod (~line 335), add:

```python
    @staticmethod
    def index_tri_scheduler_enabled() -> bool:
        """Daily 20:30 IST NSE Nifty 50 TRI refresh. Default ON; set
        ``INDEX_TRI_SCHEDULER_ENABLED=false`` (or 0/no/off) in tests/local dev."""
        raw = (_getenv("INDEX_TRI_SCHEDULER_ENABLED") or "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        return True
```

- [ ] **Step 4: Write the scheduler** — create `app/domains/mutual_funds/services/index_tri_scheduler.py`:

```python
"""Independent daily scheduler for the NSE Nifty 50 TRI refresh.

Separate AsyncIOScheduler from ``mfapi_scheduler`` (different source + cadence).
Runs ``run_tri_refresh_job`` at 20:30 IST (after NSE publishes EOD). First run
against an empty table backfills full history. Serialized across workers via a
Postgres advisory lock inside the job. Started/stopped from ``app.core.lifespan``
and gated by ``INDEX_TRI_SCHEDULER_ENABLED``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.domains.mutual_funds.services.index_tri_service import run_tri_refresh_job

logger = logging.getLogger(__name__)

INDEX_TRI_TIMEZONE = "Asia/Kolkata"
INDEX_TRI_DAILY_HOUR = 20
INDEX_TRI_DAILY_MINUTE = 30

_tri_scheduler: Optional[Any] = None


def start_tri_scheduler() -> Optional[Any]:
    global _tri_scheduler
    if _tri_scheduler is not None:
        return _tri_scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        logger.warning("apscheduler not installed; TRI daily refresh disabled. (%s)", exc)
        return None

    sched = AsyncIOScheduler(
        timezone=INDEX_TRI_TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    sched.add_job(
        run_tri_refresh_job,
        trigger=CronTrigger(
            hour=INDEX_TRI_DAILY_HOUR,
            minute=INDEX_TRI_DAILY_MINUTE,
            second=0,
            timezone=INDEX_TRI_TIMEZONE,
        ),
        id="index_tri_daily_refresh",
        name="Daily Nifty 50 TRI refresh (20:30 IST)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    sched.start()
    _tri_scheduler = sched

    for job in sched.get_jobs():
        logger.info("TRI scheduler: [%s] %s — next run: %s", job.id, job.name, job.next_run_time)
    return sched


async def shutdown_tri_scheduler() -> None:
    global _tri_scheduler
    if _tri_scheduler is None:
        return
    try:
        _tri_scheduler.shutdown(wait=False)
        logger.info("TRI scheduler shut down cleanly")
    except Exception:
        logger.exception("TRI scheduler shutdown failed")
    finally:
        _tri_scheduler = None
```

- [ ] **Step 5: Wire into lifespan** — in `app/core/lifespan.py`:

(a) Add imports near the existing mfapi_scheduler import block:

```python
from app.domains.mutual_funds.services.index_tri_scheduler import (
    shutdown_tri_scheduler,
    start_tri_scheduler,
)
```

(b) In `_start_schedulers()`, after the existing mfapi block, append:

```python
    if not get_settings().index_tri_scheduler_enabled():
        logger.info("index TRI scheduler disabled (INDEX_TRI_SCHEDULER_ENABLED is false)")
    else:
        try:
            start_tri_scheduler()
        except Exception as exc:
            logger.warning("index TRI scheduler failed to start: %s", exc)
```

(c) In `_shutdown()`, after `await shutdown_scheduler()`, add:

```python
    await shutdown_tri_scheduler()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/test_index_tri_service.py -k "tri_scheduler or scheduler_enabled" -v`
Expected: PASS.

- [ ] **Step 7: Run the full new test suite**

Run: `cd ailax/Prozpr_Backend && python -m pytest app/domains/mutual_funds/tests/ -v`
Expected: PASS (all fetcher + service + model + schema + scheduler + config tests).

- [ ] **Step 8: Commit**

```bash
git add app/domains/mutual_funds/services/index_tri_scheduler.py \
        app/core/config.py app/core/lifespan.py \
        app/domains/mutual_funds/tests/test_index_tri_service.py
git commit -m "feat(mf): add independent Nifty 50 TRI scheduler + lifespan wiring"
```

---

## Task 6: Integration verification (real endpoint + real DB)

Covers spec success criteria that need the live NSE feed and Postgres semantics
(idempotency, backfill correctness, anchor value). Run manually once; not part of CI.

**Prerequisites:** a Postgres `DATABASE_URL` with the migration applied
(`cd ailax/Prozpr_Backend && python -m alembic upgrade head`).

- [ ] **Step 1: Backfill against the live endpoint** — create a throwaway script `scripts/verify_tri_backfill.py` (delete after) or run in a REPL:

```python
import asyncio
from datetime import date
from app.core.database import _get_session_factory
from app.domains.mutual_funds.services.index_tri_service import (
    backfill_full_history, get_tri_on_or_before,
)

async def main():
    factory = _get_session_factory()
    async with factory() as db:
        n1 = await backfill_full_history(db, "NIFTY 50")
        await db.commit()
        print("inserted (first run):", n1)

        # idempotency: second run inserts 0
        n2 = await backfill_full_history(db, "NIFTY 50")
        await db.commit()
        print("inserted (second run, expect 0):", n2)

        # anchor value
        row = await get_tri_on_or_before(db, "NIFTY 50", date(2024, 1, 31))
        print("2024-01-31 TRI (expect ~31939.59):", row.tri_value, row.tri_date)

asyncio.run(main())
```

Run: `cd ailax/Prozpr_Backend && python scripts/verify_tri_backfill.py`

- [ ] **Step 2: Confirm success criteria**

Expected output:
- first run inserted ≈ 6,500.
- second run inserted **0** (idempotency / unique constraint holds).
- `2024-01-31 TRI` ≈ **31939.59**.

- [ ] **Step 3: Confirm earliest date + row count via SQL**

Run: `psql "$DATABASE_URL" -c "SELECT min(tri_date), max(tri_date), count(*) FROM index_tri_history WHERE index_name='NIFTY 50';"`
Expected: `min = 1999-06-30`, count ≈ 6,500.

- [ ] **Step 4: Remove the throwaway script (if created)**

```bash
rm -f scripts/verify_tri_backfill.py
```

- [ ] **Step 5: Commit (if anything tracked changed)** — typically nothing to commit here; verification only.

---

## Self-Review

**Spec coverage:**
- Model `index_tri_history` with `(index_name, tri_date)` unique, `tri_value` + `ntr_value` → Task 1. ✓
- Separate fetcher with retry + 2-year chunking → Task 3. ✓
- Service: backfill (full history from 1999-06-30), incremental high-water-mark, read accessor, job fn → Task 4. ✓
- Independent scheduler at 20:30 IST, own env flag + advisory lock, lifespan wiring → Task 5. ✓
- Alembic migration → Task 1, Step 7. ✓
- Verification criteria (earliest date, ≈6,500 rows, 31-Jan-2024 ≈ 31939.59, idempotency, incremental, scheduler registration) → Tasks 4 (unit) + 6 (integration). ✓

**Placeholder scan:** No TBD/TODO; every code step has full code; every command has expected output. ✓

**Type consistency:** `TriRow(tri_date, tri_value, ntr_value)` used identically across fetcher and service/tests. `fetch_tri`, `fetch_tri_chunked`, `bulk_insert_tri_rows(db, index_name, rows)`, `refresh_incremental`, `get_tri_on_or_before`, `_incremental_start`, `run_tri_refresh_job` signatures match across definition, call sites, and tests. Constants `DEFAULT_INDEX_NAME`, `NIFTY_TRI_EARLIEST`, `INDEX_TRI_LOCK_KEY` referenced consistently. ✓

**Note on dialect:** `bulk_insert_tri_rows` uses `pg_insert` (Postgres). Its idempotency is verified in Task 6 against Postgres, not on the sqlite unit-test path (service unit tests monkeypatch it). This matches the existing `nav_history_service` posture.
