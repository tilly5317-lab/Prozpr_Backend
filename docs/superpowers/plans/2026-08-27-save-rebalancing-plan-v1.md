# Save a Rebalancing Plan (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer mark a plain rebalancing run as their committed plan, serve that saved plan (else the latest run) to the portfolio page, and expose the run id in the chat response so the frontend's "Save" pill has something to post — status flip + one read + one response field.

**Architecture:** Add a nullable `origin` column to `rebalancing_runs`. A small service (`saved_plan_service`) does the two DB operations — `save_plan` (flip one run to `origin='saved'`, demote any prior saved run) and `select_current_run_id` (saved-else-latest by immutable `created_at`). Two thin router endpoints wrap them: `POST /rebalancing/{run_id}/save` and `GET /rebalancing/current` (Tasks 1–4). Task 5 forwards the already-computed rebalancing run id into `ChatSendMessageResponse` so the frontend receives it. No capture surface, no ingest hook, no change to execution or SIP (all v2). The frontend pill itself lives in the separate `Prozpr_Frontend` repo (its own plan). Spec: `docs/superpowers/specs/2026-08-27-save-rebalancing-plan-design.md`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async ORM, Alembic, Pydantic v2, pytest (`asyncio_mode=auto`), sqlite+aiosqlite for tests, httpx ASGITransport for endpoint tests.

## Global Constraints

- Run tests with `.venv-mac/bin/python -m pytest` (config in `pyproject.toml`, `asyncio_mode=auto`); `pythonpath` already includes `AI_Agents/src` and `.`.
- sqlite DB tests: `Base.metadata.create_all` FAILS (an unrelated model uses a Postgres `ARRAY`). Create only the table under test: `await conn.run_sync(RebalancingRun.__table__.create)`.
- No new ORM *model* is added (only a column), so `app/all_models.py` needs no change.
- Alembic head is `e4f5a6b7c8d9`; generate the migration with `alembic revision -m "..."` so `down_revision` is set automatically. Do **not** run `alembic upgrade` against the production RDS in `.env`; apply migrations through the normal deploy path.
- Endpoint convention: run id in the path (mirror the existing `PUT /{run_id}/status`). `GET /current` MUST be declared **before** `GET /{run_id}` or the literal `current` is parsed as a run UUID and 422s.
- Do not touch `execute_rebalance_buys`, `latest_buy_trades_by_subgroup`, or `knob_snapshot`. `origin` is `'saved'` or NULL in v1.

---

### Task 1: Add the `origin` column (model + schema + migration)

**Files:**
- Modify: `app/domains/rebalancing/models/rebalancing_run.py` (add column after `user_question`, ~line 156)
- Modify: `app/domains/rebalancing/schemas/__init__.py` (add field to `RebalancingRunListItem` ~139-148 and `RebalancingRunDetailResponse` ~156-181)
- Create: `alembic/versions/<generated>_add_origin_to_rebalancing_runs.py`
- Test: `app/domains/rebalancing/services/tests/test_saved_plan_service.py`

**Interfaces:**
- Produces: `RebalancingRun.origin: Optional[str]` (nullable, indexed); `RebalancingRunListItem.origin` and `RebalancingRunDetailResponse.origin` (`Optional[str] = None`).

- [ ] **Step 1: Write the failing test** (this file also defines the fixture + `_run()` helper reused by Tasks 2–3)

Create `app/domains/rebalancing/services/tests/test_saved_plan_service.py`:

```python
"""save_plan / select_current_run_id + the origin column (spec 2026-08-27)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun, TaxRegime

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Base.metadata.create_all FAILS on sqlite (unrelated Postgres ARRAY) —
        # create only the table under test.
        await conn.run_sync(RebalancingRun.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _run(user_id: uuid.UUID, when: datetime, **overrides) -> RebalancingRun:
    kwargs = dict(
        user_id=user_id,
        portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(),
        engine_request_id=uuid.uuid4(),
        engine_version="rebal-test",
        computed_at=when,
        tax_regime=TaxRegime.new,
        effective_tax_rate_pct=30,
        total_corpus=1_000_000,
        created_at=when,  # set explicitly; func.now() server_default isn't portable to sqlite
    )
    kwargs.update(overrides)
    return RebalancingRun(**kwargs)


async def test_origin_column_defaults_none_and_is_settable(db_session):
    run = _run(uuid.uuid4(), T0)
    db_session.add(run)
    await db_session.flush()
    assert run.origin is None
    run.origin = "saved"
    await db_session.flush()
    got = (
        await db_session.execute(
            select(RebalancingRun).where(RebalancingRun.id == run.id)
        )
    ).scalar_one()
    assert got.origin == "saved"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_saved_plan_service.py -q`
Expected: FAIL — `AttributeError`/`TypeError` on `origin` (column doesn't exist yet).

- [ ] **Step 3: Add the column to the model**

In `app/domains/rebalancing/models/rebalancing_run.py`, after the `user_question` column (~line 156), add:

```python
    # Provenance of this run. "saved" marks the customer's committed plan (set
    # by POST /rebalancing/{run_id}/save); NULL for ordinary computed runs.
    origin: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
```

(`Optional`, `String`, `Mapped`, `mapped_column` are already imported in this file.)

- [ ] **Step 4: Add the field to both response schemas**

In `app/domains/rebalancing/schemas/__init__.py`, add to `RebalancingRunListItem` (after `status: str`, ~line 143):

```python
    origin: Optional[str] = None
```

and to `RebalancingRunDetailResponse` (after `status: str`, ~line 162):

```python
    origin: Optional[str] = None
```

(`Optional` is already imported — it's used by `RebalancingRunDetailResponse`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_saved_plan_service.py -q`
Expected: PASS.

- [ ] **Step 6: Generate + fill the migration**

Run: `.venv-mac/bin/alembic revision -m "add origin to rebalancing_runs"` (this creates a file under `alembic/versions/` with `down_revision = "e4f5a6b7c8d9"`).

Edit the generated file's `upgrade()`/`downgrade()`:

```python
def upgrade() -> None:
    op.add_column(
        "rebalancing_runs",
        sa.Column("origin", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "ix_rebalancing_runs_origin", "rebalancing_runs", ["origin"]
    )


def downgrade() -> None:
    op.drop_index("ix_rebalancing_runs_origin", table_name="rebalancing_runs")
    op.drop_column("rebalancing_runs", "origin")
```

- [ ] **Step 7: Sanity-check the migration parses**

Run: `.venv-mac/bin/alembic history | head -3`
Expected: the new revision appears as head, `down_revision` = `e4f5a6b7c8d9`. (Do NOT run `alembic upgrade` against prod; it applies via deploy.)

- [ ] **Step 8: Commit**

```bash
git add app/domains/rebalancing/models/rebalancing_run.py app/domains/rebalancing/schemas/__init__.py app/domains/rebalancing/services/tests/test_saved_plan_service.py alembic/versions/
git commit -m "feat(rebalancing): add origin column for saved plans"
```

---

### Task 2: `save_plan` service (flip one run to saved, demote prior)

**Files:**
- Create: `app/domains/rebalancing/services/saved_plan_service.py`
- Test: `app/domains/rebalancing/services/tests/test_saved_plan_service.py` (append)

**Interfaces:**
- Consumes: `RebalancingRun`, the `db_session`/`_run` test fixtures from Task 1.
- Produces: `async def save_plan(db: AsyncSession, *, user_id: uuid.UUID, run_id: uuid.UUID) -> RebalancingRun | None` — flips the run to `origin='saved'`, demotes any other saved run for the user, flushes (does NOT commit), returns the run or `None` if not found/not owned.

- [ ] **Step 1: Write the failing tests** (append to `test_saved_plan_service.py`)

```python
from app.domains.rebalancing.services.saved_plan_service import save_plan


async def test_save_plan_marks_run_saved(db_session):
    uid = uuid.uuid4()
    r = _run(uid, T0)
    db_session.add(r)
    await db_session.flush()
    got = await save_plan(db_session, user_id=uid, run_id=r.id)
    assert got is not None and got.origin == "saved"


async def test_save_plan_demotes_prior_saved(db_session):
    uid = uuid.uuid4()
    old = _run(uid, T0, origin="saved")
    new = _run(uid, T0)
    db_session.add_all([old, new])
    await db_session.flush()
    await save_plan(db_session, user_id=uid, run_id=new.id)
    await db_session.refresh(old)
    await db_session.refresh(new)
    assert new.origin == "saved"
    assert old.origin is None


async def test_save_plan_is_idempotent(db_session):
    uid = uuid.uuid4()
    r = _run(uid, T0)
    db_session.add(r)
    await db_session.flush()
    await save_plan(db_session, user_id=uid, run_id=r.id)
    got = await save_plan(db_session, user_id=uid, run_id=r.id)
    assert got is not None and got.origin == "saved"


async def test_save_plan_returns_none_for_other_user(db_session):
    r = _run(uuid.uuid4(), T0)
    db_session.add(r)
    await db_session.flush()
    got = await save_plan(db_session, user_id=uuid.uuid4(), run_id=r.id)
    assert got is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_saved_plan_service.py -q`
Expected: FAIL — `ModuleNotFoundError: saved_plan_service`.

- [ ] **Step 3: Write the service**

Create `app/domains/rebalancing/services/saved_plan_service.py`:

```python
"""Save + read the customer's committed rebalancing plan (spec 2026-08-27).

v1 is a status flip on ``rebalancing_runs.origin``: exactly one run per user
carries ``origin='saved'``. No tilt capture, no CAMS survival — those are v2.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.rebalancing.models.rebalancing_run import RebalancingRun


async def save_plan(
    db: AsyncSession, *, user_id: uuid.UUID, run_id: uuid.UUID
) -> RebalancingRun | None:
    """Mark one run as the user's committed plan (``origin='saved'``).

    Demotes any other saved run for the user so exactly one stays committed.
    Returns the run, or ``None`` if it does not exist / is not this user's.
    Does NOT commit — the caller owns the transaction.
    """
    run = (
        await db.execute(
            select(RebalancingRun).where(
                RebalancingRun.id == run_id,
                RebalancingRun.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    await db.execute(
        update(RebalancingRun)
        .where(
            RebalancingRun.user_id == user_id,
            RebalancingRun.origin == "saved",
            RebalancingRun.id != run_id,
        )
        .values(origin=None)
    )
    run.origin = "saved"
    await db.flush()
    return run
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_saved_plan_service.py -q`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/saved_plan_service.py app/domains/rebalancing/services/tests/test_saved_plan_service.py
git commit -m "feat(rebalancing): save_plan service (commit a run, demote prior)"
```

---

### Task 3: `select_current_run_id` service (saved-else-latest)

**Files:**
- Modify: `app/domains/rebalancing/services/saved_plan_service.py`
- Test: `app/domains/rebalancing/services/tests/test_saved_plan_service.py` (append)

**Interfaces:**
- Produces: `async def select_current_run_id(db: AsyncSession, *, user_id: uuid.UUID) -> uuid.UUID | None` — the saved run's id if any (there is at most one), else the most-recently-**created** run's id (`created_at`, immutable), else `None`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from app.domains.rebalancing.services.saved_plan_service import select_current_run_id


async def test_select_current_none_when_empty(db_session):
    assert await select_current_run_id(db_session, user_id=uuid.uuid4()) is None


async def test_select_current_returns_only_run(db_session):
    uid = uuid.uuid4()
    r = _run(uid, T0)
    db_session.add(r)
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == r.id


async def test_select_current_prefers_saved_over_newer_plain(db_session):
    uid = uuid.uuid4()
    saved = _run(uid, T0, origin="saved")
    newer = _run(uid, T0 + timedelta(days=1))  # newer compute, but not saved
    db_session.add_all([saved, newer])
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == saved.id


async def test_select_current_latest_when_none_saved(db_session):
    uid = uuid.uuid4()
    older = _run(uid, T0)
    newer = _run(uid, T0 + timedelta(days=1))
    db_session.add_all([older, newer])
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == newer.id
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_saved_plan_service.py -q`
Expected: FAIL — `ImportError: select_current_run_id`.

- [ ] **Step 3: Add the function** (append to `saved_plan_service.py`; add `case` to the sqlalchemy import)

Change the import line to:

```python
from sqlalchemy import case, select, update
```

Append:

```python
async def select_current_run_id(
    db: AsyncSession, *, user_id: uuid.UUID
) -> uuid.UUID | None:
    """The run the portfolio page shows: the committed (saved) run if any,
    else the most-recently-created run. ``None`` if the user has no runs.

    ``case`` (not ``origin == 'saved'`` directly) keeps NULL origins sorting
    after saved regardless of the DB's NULL-ordering rules. The tie-break is
    ``created_at`` (immutable), NOT ``updated_at`` — ``updated_at`` is bumped by
    unrelated writes (e.g. ``PUT /{run_id}/status``, and the demote UPDATE), so
    keying on it could rank an old, touched run above a newer compute in the
    no-saved-plan fallback.
    """
    stmt = (
        select(RebalancingRun.id)
        .where(RebalancingRun.user_id == user_id)
        .order_by(
            case((RebalancingRun.origin == "saved", 1), else_=0).desc(),
            RebalancingRun.created_at.desc(),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_saved_plan_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/saved_plan_service.py app/domains/rebalancing/services/tests/test_saved_plan_service.py
git commit -m "feat(rebalancing): select_current_run_id (saved-else-latest)"
```

---

### Task 4: Router endpoints (`POST /{run_id}/save`, `GET /current`)

**Files:**
- Modify: `app/domains/rebalancing/routers/rebalancing_router.py`
- Create: `app/domains/rebalancing/tests/test_saved_plan_routes.py`

**Interfaces:**
- Consumes: `save_plan`, `select_current_run_id` (Tasks 2–3); `_build_asset_class_breakdown`, `RebalancingRunDetailResponse`, `RebalancingRunListItem` (existing).
- Produces: `GET /rebalancing/current` → `RebalancingRunDetailResponse`; `POST /rebalancing/{run_id}/save` → `RebalancingRunListItem`.

- [ ] **Step 1: Write the failing endpoint test**

Create `app/domains/rebalancing/tests/test_saved_plan_routes.py`:

```python
"""POST /rebalancing/{id}/save + GET /rebalancing/current, over sqlite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401
from app.core.database import get_db
from app.core.dependencies import get_effective_user
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun, TaxRegime
from app.domains.rebalancing.routers.rebalancing_router import router

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _run(when: datetime, **overrides) -> RebalancingRun:
    kwargs = dict(
        user_id=USER_ID, portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(), engine_request_id=uuid.uuid4(),
        engine_version="rebal-test", computed_at=when, tax_regime=TaxRegime.new,
        effective_tax_rate_pct=30, total_corpus=1_000_000,
        created_at=when,
    )
    kwargs.update(overrides)
    return RebalancingRun(**kwargs)


@pytest_asyncio.fixture
async def app_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(RebalancingRun.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    session = factory()
    application = FastAPI()
    application.include_router(router)

    async def _get_db():
        yield session

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[get_effective_user] = lambda: SimpleNamespace(
        id=USER_ID
    )
    try:
        yield application, session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _client(application):
    return httpx.AsyncClient(
        transport=ASGITransport(app=application), base_url="http://t"
    )


async def test_save_then_current_returns_saved_run(app_session):
    application, session = app_session
    r = _run(T0)
    session.add(r)
    await session.commit()
    async with await _client(application) as ac:
        saved = await ac.post(f"/rebalancing/{r.id}/save")
        assert saved.status_code == 200
        assert saved.json()["origin"] == "saved"

        current = await ac.get("/rebalancing/current")
        assert current.status_code == 200
        assert current.json()["id"] == str(r.id)
        assert current.json()["origin"] == "saved"  # detail schema serializes origin


async def test_save_unknown_run_404(app_session):
    application, _ = app_session
    async with await _client(application) as ac:
        resp = await ac.post(f"/rebalancing/{uuid.uuid4()}/save")
        assert resp.status_code == 404


async def test_current_404_when_no_runs(app_session):
    application, _ = app_session
    async with await _client(application) as ac:
        resp = await ac.get("/rebalancing/current")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/tests/test_saved_plan_routes.py -q`
Expected: the file fails overall (RED). Precisely: `GET /rebalancing/current` is matched by the existing `GET /{run_id}` and **422**s parsing `"current"` as a UUID; `POST /rebalancing/{uuid}/save` matches no route → **404**. Note `test_save_unknown_run_404` already sees 404 here — a false-green; it only becomes meaningful at GREEN once the route exists. The other two fail, so the file is RED overall and the TDD gate holds.

- [ ] **Step 3: Add the imports + extract the shared eager-load tuple**

In `app/domains/rebalancing/routers/rebalancing_router.py`, add to the existing imports:

```python
from app.domains.rebalancing.services.saved_plan_service import (
    save_plan,
    select_current_run_id,
)
```

Just above `list_runs` (after `router = APIRouter(...)`, ~line 45), add the shared options tuple:

```python
_DETAIL_LOADS = (
    selectinload(RebalancingRun.totals),
    selectinload(RebalancingRun.subgroup_summaries),
    selectinload(RebalancingRun.trades),
    # Load-bearing: _build_asset_class_breakdown uses fund_rows for the per-fund
    # look-through — do not prune, or the Current-vs-Target bars silently break.
    selectinload(RebalancingRun.fund_rows),
    selectinload(RebalancingRun.warnings),
    selectinload(RebalancingRun.portfolio)
    .selectinload(Portfolio.holdings)
    .selectinload(PortfolioHolding.fund_metadata),
)
```

In the existing `get_run`, replace its inline `.options(selectinload(...), ...)` block with `.options(*_DETAIL_LOADS)`.

- [ ] **Step 4: Add `GET /current` BEFORE `GET /{run_id}`**

Insert immediately after the `get_readiness` handler (before `get_run` at ~line 114):

```python
@router.get("/current", response_model=RebalancingRunDetailResponse)
async def get_current(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    run_id = await select_current_run_id(db, user_id=current_user.id)
    if run_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No rebalancing plan yet"
        )
    run = (
        await db.execute(
            select(RebalancingRun)
            .where(
                RebalancingRun.id == run_id,
                RebalancingRun.user_id == current_user.id,
            )
            .options(*_DETAIL_LOADS)
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )
    resp = RebalancingRunDetailResponse.model_validate(run)
    resp.asset_class_breakdown = _build_asset_class_breakdown(run)
    return resp
```

- [ ] **Step 5: Add `POST /{run_id}/save`**

Insert after the existing `update_status` handler (~line 217):

```python
@router.post("/{run_id}/save", response_model=RebalancingRunListItem)
async def save_run_as_plan(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    run = await save_plan(db, user_id=current_user.id, run_id=run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )
    await db.commit()
    await db.refresh(run)
    return RebalancingRunListItem.model_validate(run)
```

- [ ] **Step 6: Run the endpoint tests to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/tests/test_saved_plan_routes.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full rebalancing suite to confirm no regression**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing -q`
Expected: PASS (the extracted `_DETAIL_LOADS` must not have changed `get_run`).

- [ ] **Step 8: Commit**

```bash
git add app/domains/rebalancing/routers/rebalancing_router.py app/domains/rebalancing/tests/test_saved_plan_routes.py
git commit -m "feat(rebalancing): save-plan + current endpoints"
```

---

### Task 5: Expose the rebalancing run id in the chat response

The "Save this plan" pill (frontend) needs the run id to POST to `/rebalancing/{run_id}/save`. The brain already carries it (`ChatBrainResult.ideal_allocation_rebalancing_id`, set at `brain.py:487` from `final.rebalancing_recommendation_id`), but the HTTP response `ChatSendMessageResponse` does **not** forward it today (`chat_router.py:305-310`, `401-407` map only `asset_allocation_run_id` and `ideal_allocation_snapshot_id`). This task exposes it under the name the whole stack already uses.

**Intended side effect (accounted for in the frontend plan):** the frontend's existing "View recommended plan" pill is guarded by `Boolean(resp.ideal_allocation_rebalancing_id ?? resp.ideal_allocation_snapshot_id)` (`AIChatPanel.tsx:1588`). Because the backend never sends `ideal_allocation_rebalancing_id` today and plain rebalancing turns carry no `snapshot_id`, that pill is currently **dark** on rebalancing turns. Once this ships, it begins appearing there (the pill finally working as its own comment claims). The companion frontend plan keys the *new* "Save this plan" pill on the rebalancing id specifically, so it never fires on snapshot-only asset-allocation turns.

**Files:**
- Modify: `app/domains/chat/schemas/chat.py` (add a field to `ChatSendMessageResponse`, ~line 74 after `asset_allocation_run_id`)
- Modify: `app/domains/chat/routers/chat_router.py` (both `ChatSendMessageResponse(...)` sites, ~line 308 and ~line 404)
- Test: `app/domains/chat/tests/test_send_message_rebalancing_id.py`

**Interfaces:**
- Consumes: `ChatBrainResult.ideal_allocation_rebalancing_id` (existing).
- Produces: `ChatSendMessageResponse.ideal_allocation_rebalancing_id: Optional[uuid.UUID]` — the id the frontend posts to `POST /rebalancing/{run_id}/save`. **This name is deliberate:** the frontend already declares `ideal_allocation_rebalancing_id` (`api.ts:944`) and already reads it (`AIChatPanel.tsx:1589`); the backend response simply never sent it. Reusing the name lights up the existing (currently-dark) field instead of introducing a second, redundant `rebalancing_run_id`.

- [ ] **Step 1: Write the failing test**

Create `app/domains/chat/tests/test_send_message_rebalancing_id.py`:

```python
"""ChatSendMessageResponse exposes ideal_allocation_rebalancing_id for the Save pill."""

from __future__ import annotations

import uuid

from app.domains.chat.schemas.chat import ChatSendMessageResponse


def test_send_response_exposes_ideal_allocation_rebalancing_id():
    assert "ideal_allocation_rebalancing_id" in ChatSendMessageResponse.model_fields
    rid = uuid.uuid4()
    resp = ChatSendMessageResponse.model_construct(ideal_allocation_rebalancing_id=rid)
    assert resp.ideal_allocation_rebalancing_id == rid
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/chat/tests/test_send_message_rebalancing_id.py -q`
Expected: FAIL — `ideal_allocation_rebalancing_id` not in `model_fields`.

- [ ] **Step 3: Add the field to `ChatSendMessageResponse`**

In `app/domains/chat/schemas/chat.py`, immediately after `asset_allocation_run_id: Optional[uuid.UUID] = None`:

```python
    # The persisted rebalancing run the assistant just presented, so the client
    # can offer "Save this plan" → POST /rebalancing/{run_id}/save. The frontend
    # already declares/reads this exact name; today the backend just never sent it.
    ideal_allocation_rebalancing_id: Optional[uuid.UUID] = None
```

(`Optional` and `uuid` are already imported — `asset_allocation_run_id` uses them.)

- [ ] **Step 4: Forward it at BOTH response-construction sites**

In `app/domains/chat/routers/chat_router.py`, in each of the two `ChatSendMessageResponse(...)` constructors (the `return` at ~line 305 and the SSE `"done"` event at ~line 401), add — right after the `asset_allocation_run_id=brain_result.asset_allocation_run_id,` line:

```python
        ideal_allocation_rebalancing_id=brain_result.ideal_allocation_rebalancing_id,
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/chat/tests/test_send_message_rebalancing_id.py -q`
Expected: PASS.

- [ ] **Step 6: Run the chat suite to confirm no regression**

Run: `.venv-mac/bin/python -m pytest app/domains/chat -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/domains/chat/schemas/chat.py app/domains/chat/routers/chat_router.py app/domains/chat/tests/test_send_message_rebalancing_id.py
git commit -m "feat(chat): expose ideal_allocation_rebalancing_id in send-message response"
```

---

## Manual verification (after all tasks)

- [ ] Apply the migration in a **non-production** environment: `alembic upgrade head`, confirm `rebalancing_runs.origin` exists and is nullable; `alembic downgrade -1` then `upgrade head` to prove reversibility.
- [ ] Confirm the frontend "Save this rebalancing" button posts to `POST /rebalancing/{run_id}/save` with the `ideal_allocation_rebalancing_id` now present in the chat send-message response, and the portfolio page reads `GET /rebalancing/current`. (Frontend implementation is a separate plan in `Prozpr_Frontend`.)
- [ ] Confirm execution/SIP behavior is unchanged (no code touched them): a saved run is not treated differently by `execute_rebalance_buys` or `latest_buy_trades_by_subgroup`.
