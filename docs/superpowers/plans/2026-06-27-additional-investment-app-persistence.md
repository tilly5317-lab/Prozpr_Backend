# Additional Investment — Persistence & Invest Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each additional_investment run; the read endpoint + display schema are deferred until the Invest-page UI is designed.

**Architecture:** Mirror rebalancing persistence — write side only: AdditionalInvestmentRun ORM + targets/buys child tables (float, no tax columns), an alembic migration, and a commit-free persist service wired into the engine service. Persist each run; the read endpoint + display schema are deferred until the Invest-page UI is designed.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic v2, Alembic, pytest; AI_Agents engine via sys.path injection.

## Global Constraints

- **Mirror rebalancing/**: every file mirrors its app/domains/rebalancing/ counterpart; engine subfolder is `ainv_engine` (`ai_engine` is taken by the chat-brain domain).
- **Engine is frozen**: `AI_Agents/src/additional_investment/` already exists and is tested — import `run_additional_investment`/`AdditionalInvestmentInput`/`AdditionalInvestmentOutput` via sys.path injection (`ensure_ai_agents_path()`); do not modify the engine.
- **Money = float** (allocation family), never Decimal; do not import `_to_decimal` in the persist service.
- **I/O naming = Input/Output** (allocation family), not ComputeRequest/Response.
- **LLM calls go through LangChain / the shared formatter** — the chat reply routes through `format_with_telemetry`; the Invest-page headline is a deterministic computed-field (the only sanctioned exception).
- **Tests**: `.venv-mac/bin/python -m pytest <path> -v` from /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend; new ORM model → register in BOTH `app/all_models.py` and the domain `models/__init__.py`; sqlite tests create only the table(s) under test.
- **Intent already routes**: the classifier emits `additional_investment` (Phase 2 complete).

---

### Task 1: Additional-investment ORM models + registration

First task in Plan 3b — no earlier-task symbols are consumed. Creates the write-once, BUY-only persistence layer that the 3b persist service and detail schema will sit on top of. Mirrors the rebalancing ORM (`RebalancingRun` + children) but deliberately drops all tax/rounding/exit-load/status columns: the additional-investment engine carries no tax-lot arithmetic.

**Decisions baked in (state these in the PR description):**
- **Enums are LOCAL mirrors, not engine imports.** `TargetBucket`/`Cadence` are declared in the ORM module exactly like rebalancing declares `RebalancingRunStatus`/`TaxRegime`/`TradeAction` locally. Reason: this module is imported by `alembic/env.py` and `app/all_models.py`, which must not require `AI_Agents/src` on `sys.path`. Values are kept byte-identical to `AI_Agents/src/additional_investment/models.py` so the persist service (later 3b task) round-trips by `.value`.
- **Money is `Numeric(18,2)` fed plain `float`** (allocation family) — no `Decimal`, no `_to_decimal`.
- **No status lifecycle, no tax/rounding/exit-load columns** (BUY-only, write-once).
- `source_allocation_run_id` → the **practical** allocation run `practical_asset_allocation_runs.id` (Finding 1, Option B): the deploy split is derived from the practical allocation, so it is the run the deploy is actually based on. Persisted **inline** by the 3b-T4 orchestrator (the practical engine returns no run id), so the id is always present → the FK column is NOT NULL with `ondelete="RESTRICT"`.

**Files:**
- Create: `app/domains/additional_investment/__init__.py` (empty marker — required so the package imports)
- Create: `app/domains/additional_investment/models/__init__.py` (re-exports + `__all__`)
- Create: `app/domains/additional_investment/models/additional_investment_run.py` (`AdditionalInvestmentRun` + `AdditionalInvestmentTarget` + `AdditionalInvestmentBuy` + `TargetBucket` + `Cadence`)
- Create: `app/domains/additional_investment/models/tests/__init__.py` (empty — package marker for pytest)
- Create: `app/domains/additional_investment/models/tests/test_additional_investment_run.py` (the test below)
- Modify: `app/all_models.py:77` — insert the `additional_investment` import block immediately after the `rebalancing` block (which ends at line 77) and before the `cashflow` block (line 78)

**Interfaces:**
- Consumes: none from earlier tasks. Relies only on the engine enum **string values** (`"short_term"`, `"medium_term"`, `"long_term"`, `"lumpsum"`, `"sip_monthly"`) as a contract — the engine is NOT imported here.
- Produces (later 3b tasks rely on these exact symbols):
  - `class AdditionalInvestmentRun(Base)` — `__tablename__ = "additional_investment_runs"`; relationships `targets` / `buys` (`cascade="all, delete-orphan"`, `back_populates="run"`).
  - `class AdditionalInvestmentTarget(Base)` — `__tablename__ = "additional_investment_targets"`; columns `subgroup`, `ratio`, `target_inr`.
  - `class AdditionalInvestmentBuy(Base)` — `__tablename__ = "additional_investment_buys"`; columns `recommended_fund`, `isin`, `sub_category`, `asset_subgroup`, `rank`, `scheme_code`, `amount_inr`, `monthly_amount_inr` (nullable), `reason`.
  - `class TargetBucket(str, enum.Enum)`: `SHORT_TERM="short_term"`, `MEDIUM_TERM="medium_term"`, `LONG_TERM="long_term"`.
  - `class Cadence(str, enum.Enum)`: `LUMPSUM="lumpsum"`, `SIP_MONTHLY="sip_monthly"`.
  - `app.domains.additional_investment.models` re-exports all five.

---

- [ ] **Step 1: Write the failing test**

  Create `app/domains/additional_investment/models/tests/test_additional_investment_run.py`. Mirrors the sqlite single-table-create convention from `app/domains/ai_engine/tests/test_chat_session_state.py`. **The `import app.all_models` line is load-bearing** — it registers the FK target tables (`users`/`portfolios`/`chat_sessions`/`practical_asset_allocation_runs`) in `Base.metadata` so the DDL emitter can resolve the foreign-key columns; omit it and you get `sqlalchemy.exc.NoReferencedTableError: ... could not find table 'users'`. The referenced tables only need to exist in metadata, not physically — so we still create just the three tables under test.

  ```python
  """ORM: additional_investment_runs + targets/buys — columns, enum values, child cascade."""

  from __future__ import annotations

  import uuid

  import pytest
  import pytest_asyncio
  from sqlalchemy import select
  from sqlalchemy.ext.asyncio import (
      AsyncSession,
      async_sessionmaker,
      create_async_engine,
  )

  import app.all_models  # noqa: F401  -- registers FK target tables (users/portfolios/chat_sessions/practical_asset_allocation_runs) with Base.metadata
  from app.domains.additional_investment.models import (
      AdditionalInvestmentBuy,
      AdditionalInvestmentRun,
      AdditionalInvestmentTarget,
      TargetBucket,
      Cadence,
  )


  @pytest_asyncio.fixture
  async def db_session():
      engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
      async with engine.begin() as conn:
          # Per repo convention, Base.metadata.create_all FAILS on sqlite (an
          # unrelated model uses a Postgres ARRAY). Create only the tables under
          # test; the FK target tables only need their metadata registered (above).
          await conn.run_sync(AdditionalInvestmentRun.__table__.create)
          await conn.run_sync(AdditionalInvestmentTarget.__table__.create)
          await conn.run_sync(AdditionalInvestmentBuy.__table__.create)
      factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
      async with factory() as session:
          try:
              yield session
          finally:
              await session.rollback()
      await engine.dispose()


  def _make_run() -> AdditionalInvestmentRun:
      return AdditionalInvestmentRun(
          user_id=uuid.uuid4(),
          portfolio_id=uuid.uuid4(),
          chat_session_id=None,
          source_allocation_run_id=uuid.uuid4(),
          engine_version="ainv-1.0.0",
          target_bucket=TargetBucket.LONG_TERM,
          cadence=Cadence.SIP_MONTHLY,
          deploy_amount_inr=100000,
          deployed_inr=99800,
          undeployed_inr=200,
          request_input={"deploy_amount_inr": 100000.0},
          user_question="Where should I invest 1 lakh monthly?",
          used_cached_allocation=True,
          targets=[
              AdditionalInvestmentTarget(
                  subgroup="large_cap", ratio=0.6, target_inr=60000
              )
          ],
          buys=[
              AdditionalInvestmentBuy(
                  recommended_fund="HDFC Top 100 Fund",
                  isin="INF179K01XXX",
                  sub_category="Large Cap Fund",
                  asset_subgroup="large_cap",
                  rank=1,
                  scheme_code="120503",
                  amount_inr=59800,
                  monthly_amount_inr=5000,
                  reason="Rank-1 fund for large_cap",
              )
          ],
      )


  @pytest.mark.asyncio
  async def test_run_persists_columns_and_enum_round_trip(db_session: AsyncSession):
      run = _make_run()
      db_session.add(run)
      await db_session.flush()

      stored = (
          await db_session.execute(
              select(AdditionalInvestmentRun).where(
                  AdditionalInvestmentRun.id == run.id
              )
          )
      ).scalar_one()
      assert stored.target_bucket is TargetBucket.LONG_TERM
      assert stored.cadence is Cadence.SIP_MONTHLY
      assert float(stored.deploy_amount_inr) == 100000.0
      assert float(stored.deployed_inr) == 99800.0
      assert float(stored.undeployed_inr) == 200.0
      assert stored.engine_version == "ainv-1.0.0"
      assert stored.used_cached_allocation is True
      assert stored.request_input == {"deploy_amount_inr": 100000.0}
      assert stored.user_question == "Where should I invest 1 lakh monthly?"


  @pytest.mark.asyncio
  async def test_enum_values_match_engine_wire_contract(db_session: AsyncSession):
      # Stored DB representation must equal the engine's string values.
      assert TargetBucket.LONG_TERM.value == "long_term"
      assert TargetBucket.MEDIUM_TERM.value == "medium_term"
      assert Cadence.LUMPSUM.value == "lumpsum"
      assert Cadence.SIP_MONTHLY.value == "sip_monthly"


  @pytest.mark.asyncio
  async def test_children_persist_and_cascade_delete(db_session: AsyncSession):
      run = _make_run()
      db_session.add(run)
      await db_session.flush()

      targets = (
          await db_session.execute(select(AdditionalInvestmentTarget))
      ).scalars().all()
      buys = (
          await db_session.execute(select(AdditionalInvestmentBuy))
      ).scalars().all()
      assert len(targets) == 1
      assert len(buys) == 1
      assert targets[0].subgroup == "large_cap"
      assert float(targets[0].ratio) == 0.6
      assert buys[0].monthly_amount_inr is not None

      # delete-orphan: removing the parent removes both child collections.
      await db_session.delete(run)
      await db_session.flush()

      targets_after = (
          await db_session.execute(select(AdditionalInvestmentTarget))
      ).scalars().all()
      buys_after = (
          await db_session.execute(select(AdditionalInvestmentBuy))
      ).scalars().all()
      assert targets_after == []
      assert buys_after == []
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  .venv-mac/bin/python -m pytest app/domains/additional_investment/models/tests/test_additional_investment_run.py -v
  ```
  Expected failure: collection error `ModuleNotFoundError: No module named 'app.domains.additional_investment'` (the package and models do not exist yet).

- [ ] **Step 3: Write minimal implementation**

  Create `app/domains/additional_investment/__init__.py` (empty file — package marker):
  ```python
  ```

  Create `app/domains/additional_investment/models/tests/__init__.py` (empty file — pytest package marker):
  ```python
  ```

  Create `app/domains/additional_investment/models/additional_investment_run.py`:
  ```python
  """SQLAlchemy ORM — additional-investment engine runs and BUY-only children.

  One ``additional_investment_runs`` row per execution of the additional-investment
  engine (``AI_Agents/src/additional_investment``). Every run deploys fresh money
  (lumpsum or monthly SIP) toward the persisted PRACTICAL allocation run it is
  derived from — ``source_allocation_run_id`` references
  ``practical_asset_allocation_runs.id`` and supplies the per-bucket subgroup
  amounts the engine splits the deploy amount across.

  Children:

  - ``additional_investment_targets`` — per-subgroup deploy target (ratio + rupees),
    one row per ``SubgroupTarget``.
  - ``additional_investment_buys``    — execution-ready BUY instructions, one row per ``FundBuy``.

  BUY-only, write-once domain: NO status lifecycle and — unlike ``rebalancing_runs`` —
  NO tax / rounding / exit-load columns (the engine carries no tax-lot arithmetic).
  Money is ``Numeric(18, 2)`` fed plain floats (the allocation family), not ``Decimal``.

  The ``TargetBucket`` / ``Cadence`` enums are declared LOCALLY (mirroring how the
  rebalancing ORM declares its own enums) rather than imported from the engine: this
  module is imported by Alembic's ``env.py`` and ``app/all_models.py``, which must not
  depend on ``AI_Agents/src`` being on ``sys.path``. Their values are kept identical to
  ``AI_Agents/src/additional_investment/models.py`` so the persist service can round-trip.
  """

  from __future__ import annotations

  import enum
  import uuid
  from datetime import datetime
  from typing import TYPE_CHECKING, Any, List, Optional

  from sqlalchemy import (
      Boolean,
      DateTime,
      Enum as SAEnum,
      Float,
      ForeignKey,
      Integer,
      Numeric,
      String,
      Text,
      func,
  )
  from sqlalchemy.dialects.postgresql import JSONB, UUID
  from sqlalchemy.orm import Mapped, mapped_column, relationship

  from app.core.database import Base

  if TYPE_CHECKING:
      from app.domains.chat.models.chat import ChatSession
      from app.domains.identity.models.user import User
      from app.domains.portfolio.models.portfolio import Portfolio
      from app.domains.practical_asset_allocation.models.run import (
          PracticalAssetAllocationRun,
      )


  class TargetBucket(str, enum.Enum):
      """Subgroup-weighting branch the engine took. Values mirror
      ``additional_investment.models.TargetBucket`` exactly."""

      SHORT_TERM = "short_term"
      MEDIUM_TERM = "medium_term"
      LONG_TERM = "long_term"


  class Cadence(str, enum.Enum):
      """Deploy cadence. Values mirror ``additional_investment.models.Cadence`` exactly."""

      LUMPSUM = "lumpsum"
      SIP_MONTHLY = "sip_monthly"


  class AdditionalInvestmentRun(Base):
      """One execution of the additional-investment engine for a user's portfolio."""

      __tablename__ = "additional_investment_runs"

      id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
      )
      user_id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True),
          ForeignKey("users.id", ondelete="CASCADE"),
          nullable=False,
          index=True,
      )
      portfolio_id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True),
          ForeignKey("portfolios.id", ondelete="CASCADE"),
          nullable=False,
          index=True,
      )
      chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
          UUID(as_uuid=True),
          ForeignKey("chat_sessions.id", ondelete="SET NULL"),
          nullable=True,
          index=True,
      )
      source_allocation_run_id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True),
          ForeignKey("practical_asset_allocation_runs.id", ondelete="RESTRICT"),
          nullable=False,
          index=True,
      )

      engine_version: Mapped[str] = mapped_column(String(40), nullable=False)

      target_bucket: Mapped[TargetBucket] = mapped_column(
          SAEnum(
              TargetBucket,
              name="additional_investment_target_bucket_enum",
              create_constraint=True,
              values_callable=lambda enum_cls: [e.value for e in enum_cls],
          ),
          nullable=False,
      )
      cadence: Mapped[Cadence] = mapped_column(
          SAEnum(
              Cadence,
              name="additional_investment_cadence_enum",
              create_constraint=True,
              values_callable=lambda enum_cls: [e.value for e in enum_cls],
          ),
          nullable=False,
      )

      deploy_amount_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
      deployed_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
      undeployed_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

      request_input: Mapped[Optional[dict[str, Any]]] = mapped_column(
          JSONB, nullable=True
      )
      user_question: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
      used_cached_allocation: Mapped[Optional[bool]] = mapped_column(
          Boolean, nullable=True
      )

      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=func.now(), nullable=False
      )
      updated_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True),
          server_default=func.now(),
          onupdate=func.now(),
          nullable=False,
      )

      user: Mapped["User"] = relationship()
      portfolio: Mapped["Portfolio"] = relationship()
      chat_session: Mapped[Optional["ChatSession"]] = relationship()
      source_allocation_run: Mapped["PracticalAssetAllocationRun"] = relationship()

      targets: Mapped[List["AdditionalInvestmentTarget"]] = relationship(
          back_populates="run", cascade="all, delete-orphan"
      )
      buys: Mapped[List["AdditionalInvestmentBuy"]] = relationship(
          back_populates="run", cascade="all, delete-orphan"
      )


  class AdditionalInvestmentTarget(Base):
      """Per-subgroup deploy target for a run (mirrors engine ``SubgroupTarget``)."""

      __tablename__ = "additional_investment_targets"

      id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
      )
      run_id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True),
          ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
          nullable=False,
          index=True,
      )
      subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
      ratio: Mapped[float] = mapped_column(Float, nullable=False)
      target_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

      run: Mapped["AdditionalInvestmentRun"] = relationship(back_populates="targets")


  class AdditionalInvestmentBuy(Base):
      """One BUY instruction emitted by the engine (mirrors engine ``FundBuy``)."""

      __tablename__ = "additional_investment_buys"

      id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
      )
      run_id: Mapped[uuid.UUID] = mapped_column(
          UUID(as_uuid=True),
          ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
          nullable=False,
          index=True,
      )
      recommended_fund: Mapped[str] = mapped_column(String(255), nullable=False)
      isin: Mapped[str] = mapped_column(String(20), nullable=False)
      sub_category: Mapped[str] = mapped_column(String(80), nullable=False)
      asset_subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
      rank: Mapped[int] = mapped_column(Integer, nullable=False)
      scheme_code: Mapped[str] = mapped_column(String(40), nullable=False)
      amount_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
      monthly_amount_inr: Mapped[Optional[float]] = mapped_column(
          Numeric(18, 2), nullable=True
      )
      reason: Mapped[str] = mapped_column(Text, nullable=False)

      run: Mapped["AdditionalInvestmentRun"] = relationship(back_populates="buys")
  ```

  Create `app/domains/additional_investment/models/__init__.py`:
  ```python
  """Additional-investment engine output — runs plus per-subgroup targets and BUY rows.

  Each ``AdditionalInvestmentRun`` references the persisted PRACTICAL allocation
  run (``PracticalAssetAllocationRun`` — table ``practical_asset_allocation_runs``)
  it is derived from, which supplies the per-bucket subgroup amounts the engine
  splits the deploy amount across.
  """

  from app.domains.additional_investment.models.additional_investment_run import (
      AdditionalInvestmentBuy,
      AdditionalInvestmentRun,
      AdditionalInvestmentTarget,
      TargetBucket,
      Cadence,
  )

  __all__ = [
      "AdditionalInvestmentBuy",
      "AdditionalInvestmentRun",
      "AdditionalInvestmentTarget",
      "TargetBucket",
      "Cadence",
  ]
  ```

  Modify `app/all_models.py` — insert this block immediately after the `rebalancing` import block (which ends at line 77) and before the `cashflow` block (line 78):
  ```python
  from app.domains.additional_investment.models import (  # noqa: F401
      additional_investment_run,
  )
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  .venv-mac/bin/python -m pytest app/domains/additional_investment/models/tests/test_additional_investment_run.py -v
  ```
  Expected: `3 passed` — `test_run_persists_columns_and_enum_round_trip`, `test_enum_values_match_engine_wire_contract`, `test_children_persist_and_cascade_delete`. (Verified equivalent code passes end-to-end under sqlite+aiosqlite: single-table `.__table__.create` works for JSONB+UUID+SAEnum, the enum round-trips to its engine string value, and `session.delete(run)` cascades to both child tables.)

  Also confirm registration did not break metadata import:
  ```bash
  .venv-mac/bin/python -c "import app.all_models; from app.core.database import Base; print('additional_investment_runs' in Base.metadata.tables, 'additional_investment_targets' in Base.metadata.tables, 'additional_investment_buys' in Base.metadata.tables)"
  ```
  Expected: `True True True`.

- [ ] **Step 5: Commit**

  ```bash
  git add app/domains/additional_investment/__init__.py \
          app/domains/additional_investment/models/__init__.py \
          app/domains/additional_investment/models/additional_investment_run.py \
          app/domains/additional_investment/models/tests/__init__.py \
          app/domains/additional_investment/models/tests/test_additional_investment_run.py \
          app/all_models.py
  git commit -m "$(cat <<'EOF'
  feat(additional_investment): ORM models + Base.metadata registration (Plan 3b-T1)

  Add AdditionalInvestmentRun + AdditionalInvestmentTarget + AdditionalInvestmentBuy
  (BUY-only, write-once; no tax/rounding/status columns). Local TargetBucket/Cadence
  enums mirror the engine's string values so Alembic/all_models stay off the
  AI_Agents sys.path. Register the module in app/all_models.py.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  EOF
  )"
  ```

### Task 2: Alembic migration — additional_investment_runs + child tables + enums

Add a new Alembic revision that mirrors the rebalancing run/child block in revision `d6e7f8a90b12` for the new `additional_investment` app domain. It creates two Postgres enums and three tables (a write-once run header + two child tables), chained onto the current head. The engine is BUY-only and write-once, so there is **no status-lifecycle enum** (unlike `rebalancing_run_status_enum`).

**Decided defaults (carry into the code):**
- Money is plain `float` persisted into `sa.Numeric(18, 2)` (allocation family), not `Decimal` — no tax math here.
- `source_allocation_run_id` FKs **`practical_asset_allocation_runs.id`** with `ondelete="RESTRICT"`, NOT NULL (Finding 1, Option B). The deploy split is derived from the practical allocation, so that is the run the deploy is based on; the 3b-T4 orchestrator persists the practical run inline before the additional-investment run, so the id is always present (hence NOT NULL). This deliberately diverges from rebalancing's `source_allocation_run_id` (which FKs the goal `asset_allocation_runs`).
- `portfolio_id` is **NOT NULL** (`ondelete="CASCADE"`, indexed): the persist service always resolves it via `get_or_create_primary_portfolio`, and the detail schema requires it — ORM, migration, persist, and schema all agree.
- Enum literal values must equal the pydantic `.value`s so the 3b-T1 ORM `SAEnum(..., values_callable=...)` autogenerates no diff: `TargetBucket` -> `"short_term"`/`"medium_term"`/`"long_term"`; `Cadence` -> `"lumpsum"`/`"sip_monthly"`.
- **Mirror the 3b-T1 ORM EXACTLY (autogenerate source of truth).** The run header carries `engine_version` (`sa.String(40)`, NOT NULL) and `request_input` (`postgresql.JSONB`, nullable); the only header timestamp is `created_at` (`sa.DateTime(timezone=True)`, `server_default=sa.func.now()`, NOT NULL) — there is NO `computed_at` (Finding 5: this engine has no second timestamp to supply). The buys child carries `rank` (`sa.Integer()`, NOT NULL) and `scheme_code` (`sa.String(40)`, NOT NULL) alongside the fund-identity columns. Otherwise the child tables carry ONLY what the ORM declares: `ratio` is `sa.Float()` (not `Numeric`), and there is NO child `created_at`, NO `UNIQUE(run_id, subgroup)` on targets, NO composite `(run_id, asset_subgroup)` index on buys, and NO `server_default` on the child amount columns. This is what keeps `alembic revision --autogenerate` empty against the ORM (the truthful Task-2 verification below).

**Files:**
- **Create** `alembic/versions/c1d2e3f4a5b6_add_additional_investment_runs.py` — the migration (`revision="c1d2e3f4a5b6"`, `down_revision="f5c2a1b3d8e7"`).
- **Create** `app/domains/additional_investment/tests/__init__.py` — empty package marker.
- **Test** `app/domains/additional_investment/tests/test_additional_investment_migration.py` — loads the migration by path and exercises `upgrade()`/`downgrade()` against a recording fake `op` + stubbed enum DDL (no real Postgres, no LLM).

**Interfaces:**
- **Consumes (from earlier tasks / codebase):**
  - Task 3b-T1 ORM `AdditionalInvestmentRun` (table `additional_investment_runs`), `AdditionalInvestmentTarget` (`additional_investment_targets`), `AdditionalInvestmentBuy` (`additional_investment_buys`) — the column/FK/enum shapes this migration must reproduce so `alembic revision --autogenerate` shows an empty diff once 3b-T1 registers them in `app/all_models.py`.
  - Current alembic head `f5c2a1b3d8e7` (set as `down_revision`).
  - Live table `practical_asset_allocation_runs.id` (practical-allocation-run PK; `app/domains/practical_asset_allocation/models/run.py:42`).
- **Produces (later tasks rely on these exact DB objects):**
  - `revision = "c1d2e3f4a5b6"`, `down_revision = "f5c2a1b3d8e7"`.
  - Tables `additional_investment_runs`, `additional_investment_targets`, `additional_investment_buys` (so 3b persist + read-detail tasks have a schema to write/read).
  - Enums `additional_investment_target_bucket_enum`, `additional_investment_cadence_enum`.

---

- [ ] **Step 1: Write the failing test** — create `app/domains/additional_investment/tests/test_additional_investment_migration.py`:

```python
"""Unit test for the additional_investment Alembic migration.

Loads the migration module by file path and exercises ``upgrade()`` /
``downgrade()`` against a recording fake ``op`` plus stubbed enum create/drop,
so no real Postgres and no LLM is touched (mirrors the project's
"create only the object under test, never the whole metadata" testing rule).

Asserts: the revision is chained onto the current head; both enums are created
before any table on upgrade; the three tables are created parent->child with
their indexes; and downgrade tears everything down child->parent then drops
both enums.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "c1d2e3f4a5b6_add_additional_investment_runs.py"
)

EXPECTED_REVISION = "c1d2e3f4a5b6"
EXPECTED_DOWN_REVISION = "f5c2a1b3d8e7"  # current alembic head


class _FakeOp:
    """Records the schema operations the migration requests; runs no DDL."""

    def __init__(self, ops: list):
        self._ops = ops

    def get_bind(self):
        return "FAKE_BIND"

    def create_table(self, name, *cols, **kw):
        self._ops.append(("create_table", name))

    def create_index(self, name, table_name, columns, **kw):
        self._ops.append(("create_index", name, table_name))

    def drop_table(self, name):
        self._ops.append(("drop_table", name))

    def drop_index(self, name, table_name=None, **kw):
        self._ops.append(("drop_index", name, table_name))

    def execute(self, statement):
        self._ops.append(("execute", str(statement)))


def _load_migration(ops: list):
    """Load a fresh copy of the migration with op + enum DDL stubbed out."""
    spec = importlib.util.spec_from_file_location(
        "ainv_migration_under_test", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # FileNotFoundError until the migration exists

    module.op = _FakeOp(ops)
    for attr in ("ADDITIONAL_INVESTMENT_TARGET_BUCKET", "ADDITIONAL_INVESTMENT_CADENCE"):
        enum_obj = getattr(module, attr)
        enum_obj.create = (
            lambda bind, checkfirst=False, _n=enum_obj.name: ops.append(
                ("enum_create", _n)
            )
        )
        enum_obj.drop = (
            lambda bind, checkfirst=False, _n=enum_obj.name: ops.append(
                ("enum_drop", _n)
            )
        )
    return module


def test_revision_is_chained_onto_current_head():
    ops: list = []
    module = _load_migration(ops)
    assert module.revision == EXPECTED_REVISION
    assert module.down_revision == EXPECTED_DOWN_REVISION


def test_upgrade_creates_both_enums_before_any_table():
    ops: list = []
    module = _load_migration(ops)
    module.upgrade()

    kinds = [row[0] for row in ops]
    first_table = kinds.index("create_table")
    enum_creates = [row[1] for row in ops if row[0] == "enum_create"]
    assert enum_creates == [
        "additional_investment_target_bucket_enum",
        "additional_investment_cadence_enum",
    ]
    assert all(
        i < first_table for i, row in enumerate(ops) if row[0] == "enum_create"
    )


def test_upgrade_creates_tables_parent_then_children_with_indexes():
    ops: list = []
    module = _load_migration(ops)
    module.upgrade()

    created_tables = [row[1] for row in ops if row[0] == "create_table"]
    assert created_tables == [
        "additional_investment_runs",
        "additional_investment_targets",
        "additional_investment_buys",
    ]

    created_indexes = {row[1] for row in ops if row[0] == "create_index"}
    assert {
        "ix_additional_investment_runs_user_id",
        "ix_additional_investment_runs_portfolio_id",
        "ix_additional_investment_runs_chat_session_id",
        "ix_additional_investment_runs_source_allocation_run_id",
        "ix_additional_investment_targets_run_id",
        "ix_additional_investment_buys_run_id",
    } <= created_indexes


def test_downgrade_drops_children_before_parent_then_enums():
    ops: list = []
    module = _load_migration(ops)
    module.downgrade()

    dropped_tables = [row[1] for row in ops if row[0] == "drop_table"]
    assert dropped_tables == [
        "additional_investment_buys",
        "additional_investment_targets",
        "additional_investment_runs",
    ]

    enum_drops = [row[1] for row in ops if row[0] == "enum_drop"]
    assert enum_drops == [
        "additional_investment_cadence_enum",
        "additional_investment_target_bucket_enum",
    ]
    last_table_drop = max(i for i, row in enumerate(ops) if row[0] == "drop_table")
    first_enum_drop = min(i for i, row in enumerate(ops) if row[0] == "enum_drop")
    assert last_table_drop < first_enum_drop
```

  Also create the empty package marker `app/domains/additional_investment/tests/__init__.py`:

```python
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/tests/test_additional_investment_migration.py -v
```

  Expected failure: every test errors with `FileNotFoundError: [Errno 2] No such file or directory: '.../alembic/versions/c1d2e3f4a5b6_add_additional_investment_runs.py'` raised inside `_load_migration` (the migration file does not exist yet).

- [ ] **Step 3: Write minimal implementation** — create `alembic/versions/c1d2e3f4a5b6_add_additional_investment_runs.py`:

```python
"""Add additional_investment_runs and its two child tables (BUY-only deploy engine).

Mirrors the rebalancing run/child family (revision d6e7f8a90b12) for the
additional-investment app domain: a write-once run header plus two children -
the per-subgroup deploy targets and the per-fund BUY list. The engine is
BUY-only and write-once, so there is no status-lifecycle enum.

``source_allocation_run_id`` FKs the live ``practical_asset_allocation_runs``
table with ondelete RESTRICT, NOT NULL (Finding 1, Option B): the deploy split is
derived from the practical allocation, which the orchestrator persists inline so
the id is always present. Money is float into Numeric(18, 2) - the allocation
family, not Rebalancing's Decimal - because there is no tax-lot math here.

Revision ID: c1d2e3f4a5b6
Revises: f5c2a1b3d8e7
Create Date: 2026-06-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "f5c2a1b3d8e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Enum definitions (created once; reused on the run table) --

ADDITIONAL_INVESTMENT_TARGET_BUCKET = postgresql.ENUM(
    "short_term",
    "medium_term",
    "long_term",
    name="additional_investment_target_bucket_enum",
    create_type=False,
)
ADDITIONAL_INVESTMENT_CADENCE = postgresql.ENUM(
    "lumpsum",
    "sip_monthly",
    name="additional_investment_cadence_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ADDITIONAL_INVESTMENT_TARGET_BUCKET.create(bind, checkfirst=True)
    ADDITIONAL_INVESTMENT_CADENCE.create(bind, checkfirst=True)

    # 1. additional_investment_runs (write-once header; BUY-only, no status enum).
    op.create_table(
        "additional_investment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_allocation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("practical_asset_allocation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column("target_bucket", ADDITIONAL_INVESTMENT_TARGET_BUCKET, nullable=False),
        sa.Column("cadence", ADDITIONAL_INVESTMENT_CADENCE, nullable=False),
        sa.Column("deploy_amount_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("deployed_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("undeployed_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("request_input", postgresql.JSONB, nullable=True),
        sa.Column("used_cached_allocation", sa.Boolean(), nullable=True),
        sa.Column("user_question", sa.String(2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_additional_investment_runs_user_id",
        "additional_investment_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_additional_investment_runs_portfolio_id",
        "additional_investment_runs",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_additional_investment_runs_chat_session_id",
        "additional_investment_runs",
        ["chat_session_id"],
    )
    op.create_index(
        "ix_additional_investment_runs_source_allocation_run_id",
        "additional_investment_runs",
        ["source_allocation_run_id"],
    )

    # 2. additional_investment_targets (one row per SubgroupTarget).
    op.create_table(
        "additional_investment_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subgroup", sa.String(80), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("target_inr", sa.Numeric(18, 2), nullable=False),
    )
    op.create_index(
        "ix_additional_investment_targets_run_id",
        "additional_investment_targets",
        ["run_id"],
    )

    # 3. additional_investment_buys (one row per FundBuy).
    op.create_table(
        "additional_investment_buys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recommended_fund", sa.String(255), nullable=False),
        sa.Column("isin", sa.String(20), nullable=False),
        sa.Column("sub_category", sa.String(80), nullable=False),
        sa.Column("asset_subgroup", sa.String(80), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("scheme_code", sa.String(40), nullable=False),
        sa.Column("amount_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("monthly_amount_inr", sa.Numeric(18, 2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_additional_investment_buys_run_id",
        "additional_investment_buys",
        ["run_id"],
    )


def downgrade() -> None:
    # Drop children before the parent, then the enums.
    op.drop_index(
        "ix_additional_investment_buys_run_id",
        table_name="additional_investment_buys",
    )
    op.drop_table("additional_investment_buys")

    op.drop_index(
        "ix_additional_investment_targets_run_id",
        table_name="additional_investment_targets",
    )
    op.drop_table("additional_investment_targets")

    op.drop_index(
        "ix_additional_investment_runs_source_allocation_run_id",
        table_name="additional_investment_runs",
    )
    op.drop_index(
        "ix_additional_investment_runs_chat_session_id",
        table_name="additional_investment_runs",
    )
    op.drop_index(
        "ix_additional_investment_runs_portfolio_id",
        table_name="additional_investment_runs",
    )
    op.drop_index(
        "ix_additional_investment_runs_user_id",
        table_name="additional_investment_runs",
    )
    op.drop_table("additional_investment_runs")

    bind = op.get_bind()
    ADDITIONAL_INVESTMENT_CADENCE.drop(bind, checkfirst=True)
    ADDITIONAL_INVESTMENT_TARGET_BUCKET.drop(bind, checkfirst=True)
```

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/tests/test_additional_investment_migration.py -v
```

  Expected: `4 passed` — `test_revision_is_chained_onto_current_head`, `test_upgrade_creates_both_enums_before_any_table`, `test_upgrade_creates_tables_parent_then_children_with_indexes`, `test_downgrade_drops_children_before_parent_then_enums`.

  Real-DB confirmation (requires a configured Postgres + `app.core.config` DSN; run after Task 3b-T1 registers the ORM so autogenerate has both sides to compare). Exact commands, from the repo root:

```
.venv-mac/bin/alembic heads
.venv-mac/bin/alembic upgrade head
.venv-mac/bin/alembic downgrade -1
.venv-mac/bin/alembic upgrade head
.venv-mac/bin/alembic revision --autogenerate -m "verify additional_investment parity"
```

  Expected: `alembic heads` shows the single head `c1d2e3f4a5b6 (head)`; `upgrade head` then `downgrade -1` run clean (the three tables + two enums are created then fully dropped); the final `--autogenerate` produces an **empty** `upgrade()`/`downgrade()` (no diff vs the 3b-T1 ORM) — delete that scratch revision file afterward.

- [ ] **Step 5: Commit**

```
git add alembic/versions/c1d2e3f4a5b6_add_additional_investment_runs.py \
        app/domains/additional_investment/tests/__init__.py \
        app/domains/additional_investment/tests/test_additional_investment_migration.py
git commit -m "$(cat <<'EOF'
feat(additional_investment): add Alembic migration for run + child tables

Create additional_investment_runs (write-once, BUY-only header) plus
additional_investment_targets and additional_investment_buys child tables,
and the additional_investment_target_bucket_enum / additional_investment_cadence_enum
Postgres enums. Mirrors the rebalancing block in d6e7f8a90b12; chained onto
head f5c2a1b3d8e7. source_allocation_run_id FKs practical_asset_allocation_runs
(Option B), NOT NULL.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 3: Additional-investment persist service

Write the persistence surface for the `additional_investment` domain. It takes an `AdditionalInvestmentOutput` from the pure engine and writes one `AdditionalInvestmentRun` row plus its `AdditionalInvestmentTarget` and `AdditionalInvestmentBuy` children. Mirrors `persist_rebalancing_recommendation` exactly, with two deliberate departures (per the Phase-3 contract).

**Decided-default notes (keep these as code comments in the implementation):**
- **Money = float (allocation family), NOT Decimal.** Floats flow straight into `Numeric(18, 2)` columns; do NOT import or use `_to_decimal` (there is no tax-lot arithmetic here).
- **No status-lifecycle enum.** `AdditionalInvestmentRun` is BUY-only / write-once — there is no `status` field to set (contrast `RebalancingRun.status`).
- `target_bucket` and `cadence` are persisted as the enum **`.value` strings** into the `SAEnum` (native enum) columns defined on the 3b-T1 ORM; the persist service passes `output.target_bucket.value` / `output.cadence.value`, which round-trip through each column's `values_callable`.
- `monthly_amount_inr` is copied straight off each `FundBuy` — the engine already sets it only for `Cadence.SIP_MONTHLY` (None for lumpsum), so persistence does no cadence branching.
- **`engine_version` is stamped from `AINV_ENGINE_VERSION`** (resolves the NOT-NULL gap: the ORM `engine_version` column is `nullable=False` with no `server_default`). The constant `AINV_ENGINE_VERSION = "ainv-1.0.0"` is defined in the Plan-3a engine adapter `ainv_engine/service.py`; the persist service imports it **lazily inside the function** (so this module never top-level-imports `service.py` — Plan 3b-T4 makes `service.py` top-level-import THIS module, and a mutual top-level import would cycle) and sets `engine_version=AINV_ENGINE_VERSION`. `created_at` is filled by the column's `server_default=func.now()` (the only timestamp this engine supplies — there is no `computed_at`, Finding 5), so the persist service does NOT set it.
- **`rank` + `scheme_code` are recovered by an isin join.** The engine `FundBuy` carries neither, so the persist service builds `{rf.isin: rf for rf in request.ranked_funds}` from `request` (an `AdditionalInvestmentInput`, passed as `request=inp` by 3b-T4) and fills `rank=rf.rank` / `scheme_code=rf.scheme_code` per buy. Every buy's isin is a ranked fund by construction, so `request` is required whenever there are buys.

**Files:**
- Create: `app/domains/additional_investment/services/additional_investment_persist_service.py`
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (Plan 3a) — add the module constant `AINV_ENGINE_VERSION = "ainv-1.0.0"` consumed lazily by the persist service.
- Create (test): `app/domains/additional_investment/services/ainv_engine/tests/test_persist.py`
- Prereq (from 3b-T1, do NOT create here): `app/domains/additional_investment/models/__init__.py` re-exporting `AdditionalInvestmentRun`, `AdditionalInvestmentTarget`, `AdditionalInvestmentBuy`; those modules registered in `app/all_models.py:71` block (alongside the rebalancing models import).
- Prereq (from 3b-T2 scaffolding): `app/domains/additional_investment/services/ainv_engine/tests/__init__.py` (empty marker) and `app/domains/additional_investment/services/__init__.py` exist.

**Interfaces:**
- Consumes (from 3b-T1): ORM `AdditionalInvestmentRun` (table `additional_investment_runs`; columns `user_id`, `portfolio_id`, `chat_session_id`, `source_allocation_run_id`, `target_bucket: SAEnum(TargetBucket)`, `cadence: SAEnum(Cadence)`, `deploy_amount_inr`/`deployed_inr`/`undeployed_inr: Numeric(18,2)`, `used_cached_allocation: bool nullable`, `user_question: String nullable`, `request_input: JSONB nullable`, `id: UUID pk default uuid4`); `AdditionalInvestmentTarget` (`run_id`, `subgroup`, `ratio`, `target_inr`); `AdditionalInvestmentBuy` (`run_id`, `recommended_fund`, `isin`, `sub_category`, `asset_subgroup`, `rank: Integer`, `scheme_code: String(40)`, `amount_inr`, `monthly_amount_inr: Numeric nullable`, `reason`).
- Consumes (engine, AI_Agents/src via `ensure_ai_agents_path()`): `additional_investment.models.AdditionalInvestmentOutput`, `AdditionalInvestmentInput`, `RankedFund`, `SubgroupTarget`, `FundBuy`, `TargetBucket`, `Cadence`.
- Consumes (existing app): `get_or_create_primary_portfolio(db, user_id)` from `app/domains/portfolio/services/portfolio_service.py:31`; `ensure_ai_agents_path()` from `app/domains/ai_engine/common.py:47`.
- Consumes (from the 3a engine adapter, added by this task): `AINV_ENGINE_VERSION` — module constant in `app/domains/additional_investment/services/ainv_engine/service.py`; imported lazily inside `persist_additional_investment_recommendation` and written to `engine_version`.
- Produces (3b-T4 orchestrator `compute_additional_investment_result` relies on this EXACT signature):
  ```python
  async def persist_additional_investment_recommendation(
      db: AsyncSession,
      user_id: uuid.UUID,
      output: AdditionalInvestmentOutput,
      *,
      source_allocation_run_id: uuid.UUID,
      chat_session_id: Optional[uuid.UUID] = None,
      used_cached_allocation: bool = False,
      user_question: Optional[str] = None,
      request: Optional[AdditionalInvestmentInput] = None,
  ) -> uuid.UUID
  ```

---

- [ ] **Step 1: Write the failing test**

  Create `app/domains/additional_investment/services/ainv_engine/tests/test_persist.py`. Uses plain fakes only — a `_FakeSession` capturing `add()`/`flush()` and a monkeypatched `get_or_create_primary_portfolio` — no real DB and no LLM. `from app import all_models` populates the full mapper registry so `configure_mappers()` (triggered registry-wide on first ORM instantiation) can resolve the string-based `relationship()` targets.

  ```python
  """Persist an additional-investment engine result across the normalized
  additional_investment_* tables. Fakes only: no real DB, no LLM."""

  import uuid

  import pytest

  # Populate the full ORM mapper registry so configure_mappers() (run
  # registry-wide on first instantiation below) can resolve every
  # string-based relationship() target without a live DB.
  from app import all_models  # noqa: F401

  from app.domains.ai_engine.common import ensure_ai_agents_path

  ensure_ai_agents_path()

  from additional_investment.models import (  # noqa: E402
      AdditionalInvestmentInput,
      AdditionalInvestmentOutput,
      TargetBucket,
      Cadence,
      FundBuy,
      RankedFund,
      SubgroupTarget,
  )


  class _FakePortfolio:
      def __init__(self):
          self.id = uuid.uuid4()


  class _FakeSession:
      """Minimal AsyncSession stand-in: records add()s and assigns a uuid id
      to every flushed row whose id is still unset (the real INSERT default)."""

      def __init__(self):
          self.added = []
          self.flush_count = 0

      def add(self, obj):
          self.added.append(obj)

      async def flush(self):
          self.flush_count += 1
          for obj in self.added:
              if getattr(obj, "id", None) is None:
                  obj.id = uuid.uuid4()


  async def _fake_get_or_create_primary_portfolio(db, user_id):
      return _FakePortfolio()


  def _sip_output():
      return AdditionalInvestmentOutput(
          target_bucket=TargetBucket.LONG_TERM,
          cadence=Cadence.SIP_MONTHLY,
          deploy_amount_inr=120000.0,
          deployed_inr=120000.0,
          undeployed_inr=0.0,
          per_subgroup_target=[
              SubgroupTarget(subgroup="low_beta_equities", ratio=0.6, target_inr=72000.0),
              SubgroupTarget(subgroup="high_beta_equities", ratio=0.4, target_inr=48000.0),
          ],
          buys=[
              FundBuy(
                  recommended_fund="Fund A",
                  isin="INF001",
                  sub_category="Large Cap Fund",
                  asset_subgroup="low_beta_equities",
                  amount_inr=72000.0,
                  monthly_amount_inr=6000.0,
                  reason="rank-1 fresh buy",
              ),
              FundBuy(
                  recommended_fund="Fund B",
                  isin="INF002",
                  sub_category="Mid Cap Fund",
                  asset_subgroup="high_beta_equities",
                  amount_inr=48000.0,
                  monthly_amount_inr=4000.0,
                  reason="rank-1 fresh buy",
              ),
          ],
      )


  def _sip_request():
      """Engine input whose ranked_funds carry the rank + scheme_code the persist
      service joins onto each buy by isin."""
      return AdditionalInvestmentInput(
          deploy_amount_inr=120000.0,
          cadence=Cadence.SIP_MONTHLY,
          subgroups=[],
          short_term_fulfilled=True,
          medium_term_fulfilled=True,
          ranked_funds=[
              RankedFund(
                  asset_subgroup="low_beta_equities",
                  sub_category="Large Cap Fund",
                  rank=1,
                  isin="INF001",
                  scheme_code="SC001",
                  recommended_fund="Fund A",
              ),
              RankedFund(
                  asset_subgroup="high_beta_equities",
                  sub_category="Mid Cap Fund",
                  rank=2,
                  isin="INF002",
                  scheme_code="SC002",
                  recommended_fund="Fund B",
              ),
          ],
      )


  @pytest.mark.asyncio
  async def test_persist_writes_run_targets_and_buys(monkeypatch):
      from app.domains.additional_investment.services import (
          additional_investment_persist_service as svc,
      )

      monkeypatch.setattr(
          svc, "get_or_create_primary_portfolio", _fake_get_or_create_primary_portfolio
      )

      from app.domains.additional_investment.models import (
          AdditionalInvestmentBuy,
          AdditionalInvestmentRun,
          AdditionalInvestmentTarget,
      )

      db = _FakeSession()
      user_id = uuid.uuid4()
      source_id = uuid.uuid4()

      run_id = await svc.persist_additional_investment_recommendation(
          db,
          user_id,
          _sip_output(),
          source_allocation_run_id=source_id,
          chat_session_id=None,
          used_cached_allocation=True,
          user_question="invest 10k monthly",
          request=_sip_request(),
      )

      assert isinstance(run_id, uuid.UUID)

      runs = [o for o in db.added if isinstance(o, AdditionalInvestmentRun)]
      targets = [o for o in db.added if isinstance(o, AdditionalInvestmentTarget)]
      buys = [o for o in db.added if isinstance(o, AdditionalInvestmentBuy)]

      assert len(runs) == 1
      run = runs[0]
      assert run.id == run_id
      assert run.user_id == user_id
      assert run.source_allocation_run_id == source_id
      assert run.target_bucket == "long_term"   # enum .value persisted as String
      assert run.cadence == "sip_monthly"
      assert run.deploy_amount_inr == 120000.0  # float straight into Numeric
      assert run.deployed_inr == 120000.0
      assert run.undeployed_inr == 0.0
      assert run.used_cached_allocation is True
      assert run.user_question == "invest 10k monthly"
      assert run.engine_version == "ainv-1.0.0"  # stamped from AINV_ENGINE_VERSION

      # N targets, all parented to the flushed run.
      assert len(targets) == 2
      assert {t.subgroup for t in targets} == {"low_beta_equities", "high_beta_equities"}
      assert all(t.run_id == run_id for t in targets)
      assert {round(t.ratio, 2) for t in targets} == {0.6, 0.4}

      # M buys, parented to the run; monthly set because cadence == sip_monthly.
      assert len(buys) == 2
      assert all(b.run_id == run_id for b in buys)
      assert {b.isin for b in buys} == {"INF001", "INF002"}
      assert all(b.monthly_amount_inr is not None for b in buys)

      # rank + scheme_code recovered from request.ranked_funds by isin join.
      buys_by_isin = {b.isin: b for b in buys}
      assert buys_by_isin["INF001"].rank == 1
      assert buys_by_isin["INF001"].scheme_code == "SC001"
      assert buys_by_isin["INF002"].rank == 2
      assert buys_by_isin["INF002"].scheme_code == "SC002"

      # Two flushes: one to obtain run.id, one for the children.
      assert db.flush_count == 2


  @pytest.mark.asyncio
  async def test_persist_lumpsum_buys_have_no_monthly_amount(monkeypatch):
      from app.domains.additional_investment.services import (
          additional_investment_persist_service as svc,
      )

      monkeypatch.setattr(
          svc, "get_or_create_primary_portfolio", _fake_get_or_create_primary_portfolio
      )

      from app.domains.additional_investment.models import AdditionalInvestmentBuy

      output = AdditionalInvestmentOutput(
          target_bucket=TargetBucket.MEDIUM_TERM,
          cadence=Cadence.LUMPSUM,
          deploy_amount_inr=50000.0,
          deployed_inr=50000.0,
          undeployed_inr=0.0,
          per_subgroup_target=[
              SubgroupTarget(subgroup="low_beta_equities", ratio=1.0, target_inr=50000.0),
          ],
          buys=[
              FundBuy(
                  recommended_fund="Fund A",
                  isin="INF001",
                  sub_category="Large Cap Fund",
                  asset_subgroup="low_beta_equities",
                  amount_inr=50000.0,
                  reason="rank-1 fresh buy",
              ),
          ],
      )

      request = AdditionalInvestmentInput(
          deploy_amount_inr=50000.0,
          cadence=Cadence.LUMPSUM,
          subgroups=[],
          short_term_fulfilled=True,
          medium_term_fulfilled=True,
          ranked_funds=[
              RankedFund(
                  asset_subgroup="low_beta_equities",
                  sub_category="Large Cap Fund",
                  rank=1,
                  isin="INF001",
                  scheme_code="SC001",
                  recommended_fund="Fund A",
              ),
          ],
      )

      db = _FakeSession()
      run_id = await svc.persist_additional_investment_recommendation(
          db,
          uuid.uuid4(),
          output,
          source_allocation_run_id=uuid.uuid4(),
          request=request,
      )

      assert isinstance(run_id, uuid.UUID)
      buys = [o for o in db.added if isinstance(o, AdditionalInvestmentBuy)]
      assert len(buys) == 1
      assert buys[0].run_id == run_id
      assert buys[0].rank == 1  # joined from request.ranked_funds by isin
      assert buys[0].scheme_code == "SC001"
      assert buys[0].monthly_amount_inr is None  # lumpsum -> no monthly framing
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  .venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_persist.py -v
  ```
  Expected failure: collection/import error — `ModuleNotFoundError: No module named 'app.domains.additional_investment.services.additional_investment_persist_service'` (the module under test does not exist yet). Run from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`.

- [ ] **Step 3: Write minimal implementation**

  First, add the engine-version constant to the Plan-3a orchestrator `app/domains/additional_investment/services/ainv_engine/service.py` — insert it next to the existing module-level constants (e.g. right after `_MSG_ENGINE_ERROR`):

  ```python
  # Stamped onto every persisted AdditionalInvestmentRun.engine_version. Bump when
  # the additional-investment engine's output contract changes.
  AINV_ENGINE_VERSION = "ainv-1.0.0"
  ```

  Create `app/domains/additional_investment/services/additional_investment_persist_service.py`:

  ```python
  """Persist an additional-investment engine result across the normalized
  ``additional_investment_*`` tables.

  A run always references the persisted asset-allocation run whose per-bucket
  subgroups it deployed fresh money into -- ``source_allocation_run_id`` is
  required.

  Money is plain ``float`` (rupees), matching the allocation family this engine
  composes with (practical_asset_allocation), NOT the ``Decimal`` used by
  Rebalancing: floats flow straight into the ``Numeric(18, 2)`` columns. There
  is no tax-lot arithmetic here, so ``_to_decimal`` is deliberately NOT used.

  Commit-free: the caller owns the transaction (mirrors
  ``persist_rebalancing_recommendation``).
  """

  from __future__ import annotations

  import uuid
  from typing import Any, Optional

  from sqlalchemy.ext.asyncio import AsyncSession

  from app.domains.additional_investment.models import (
      AdditionalInvestmentBuy,
      AdditionalInvestmentRun,
      AdditionalInvestmentTarget,
  )
  from app.domains.ai_engine.common import ensure_ai_agents_path
  from app.domains.portfolio.services.portfolio_service import (
      get_or_create_primary_portfolio,
  )

  ensure_ai_agents_path()

  from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
      AdditionalInvestmentInput,
      AdditionalInvestmentOutput,
  )


  async def persist_additional_investment_recommendation(
      db: AsyncSession,
      user_id: uuid.UUID,
      output: AdditionalInvestmentOutput,
      *,
      source_allocation_run_id: uuid.UUID,
      chat_session_id: Optional[uuid.UUID] = None,
      used_cached_allocation: bool = False,
      user_question: Optional[str] = None,
      request: Optional[AdditionalInvestmentInput] = None,
  ) -> uuid.UUID:
      """Write the engine output and return the new ``AdditionalInvestmentRun`` id.

      BUY-only / write-once: there is no status-lifecycle field to set (contrast
      ``RebalancingRun.status``).
      """
      # ``AINV_ENGINE_VERSION`` lives in the engine adapter (ainv_engine/service.py).
      # Import it lazily so this module never top-level-imports service.py: Plan
      # 3b-T4 makes service.py top-level-import THIS module, and a mutual top-level
      # import would cycle.
      from app.domains.additional_investment.services.ainv_engine.service import (
          AINV_ENGINE_VERSION,
      )

      portfolio = await get_or_create_primary_portfolio(db, user_id)

      # ``request`` is optional but recommended so the per-call engine input is
      # captured for audit. Serialise to JSON-safe primitives for the JSONB column.
      request_input: Optional[dict[str, Any]] = (
          request.model_dump(mode="json") if request is not None else None
      )

      run = AdditionalInvestmentRun(
          user_id=user_id,
          portfolio_id=portfolio.id,
          chat_session_id=chat_session_id,
          source_allocation_run_id=source_allocation_run_id,
          engine_version=AINV_ENGINE_VERSION,
          # SAEnum columns persist the pydantic ``.value`` strings.
          target_bucket=output.target_bucket.value,
          cadence=output.cadence.value,
          # Floats straight into Numeric(18, 2) -- NO _to_decimal.
          deploy_amount_inr=output.deploy_amount_inr,
          deployed_inr=output.deployed_inr,
          undeployed_inr=output.undeployed_inr,
          used_cached_allocation=used_cached_allocation,
          user_question=user_question,
          request_input=request_input,
      )
      db.add(run)
      await db.flush()  # assign run.id before parenting children

      for target in output.per_subgroup_target:
          db.add(
              AdditionalInvestmentTarget(
                  run_id=run.id,
                  subgroup=target.subgroup,
                  ratio=target.ratio,
                  target_inr=target.target_inr,
              )
          )

      # ``rank`` + ``scheme_code`` are not on the engine ``FundBuy``; recover them
      # by joining each buy's isin against the request's ``ranked_funds`` (every
      # buy's isin is a ranked fund by construction), so ``request`` is required
      # whenever there are buys.
      ranked_by_isin = {
          rf.isin: rf for rf in (request.ranked_funds if request is not None else [])
      }

      for buy in output.buys:
          ranked = ranked_by_isin[buy.isin]
          db.add(
              AdditionalInvestmentBuy(
                  run_id=run.id,
                  recommended_fund=buy.recommended_fund,
                  isin=buy.isin,
                  sub_category=buy.sub_category,
                  asset_subgroup=buy.asset_subgroup,
                  rank=ranked.rank,
                  scheme_code=ranked.scheme_code,
                  amount_inr=buy.amount_inr,
                  # Already None for lumpsum, set for sip_monthly by the engine.
                  monthly_amount_inr=buy.monthly_amount_inr,
                  reason=buy.reason,
              )
          )

      await db.flush()
      return run.id
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  .venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_persist.py -v
  ```
  Expected: `2 passed` — `test_persist_writes_run_targets_and_buys` and `test_persist_lumpsum_buys_have_no_monthly_amount` both PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add app/domains/additional_investment/services/additional_investment_persist_service.py \
          app/domains/additional_investment/services/ainv_engine/tests/test_persist.py
  git commit -m "feat(additional_investment): persist service for runs/targets/buys

  Mirror persist_rebalancing_recommendation: get_or_create_primary_portfolio,
  build AdditionalInvestmentRun (floats straight into Numeric, no _to_decimal),
  flush, write per_subgroup_target + buys children, flush, return run.id.
  Commit-free; caller owns the transaction.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

### Task 4: Wire persistence into the additional-investment engine service

The orchestrator `compute_additional_investment_result` (Plan 3a Task 4) runs the practical allocation → builds the engine input → runs the BUY-only engine (output bound to `response`) → builds a facts pack, and already carries an `if persist:` block that defaults OFF (`persist=False`) and **lazily** calls `persist_additional_investment_recommendation(...)` with `source_allocation_run_id=None`. This task promotes that persist import to module scope (so the tests can patch it on the service namespace), persists the PRACTICAL allocation run inline to capture the real `source_allocation_run_id` (Finding 1, Option B — `compute_practical_allocation_result` returns no run id, so we must persist it ourselves), gates the write on a live chat session, and flips the chat handler call to `persist=True` so the persisted run id reaches the HTTP layer as `ModuleOutput.persisted_run_id`.

**Decided-default notes (state once, here):**
- **Money is `float`** (allocation family). Pass the engine `output`'s float amounts straight into `persist_additional_investment_recommendation` (which writes `Numeric(18,2)`). Do **NOT** import `_to_decimal` here.
- **`source_allocation_run_id` is the practical allocation run, persisted inline (Finding 1, Option B).** The deploy split is derived from the *practical* allocation, so that is the run the deploy is actually based on. `compute_practical_allocation_result` returns no run id (unlike rebalancing's `AllocationRunOutcome.asset_allocation_run_id`), so the orchestrator calls `persist_practical_allocation_run(...)` itself to obtain it — the only practical persist in the ainv path (it does not route through `paa_engine/chat.py`), so no double-write. The id is always produced ⇒ the FK column is NOT NULL.
- **`build_ainv_facts_pack` lives in `ainv_engine/chat.py`** (Plan 3a, D6); `service.compute_additional_investment_result` imports it **lazily** (`from app.domains.additional_investment.services.ainv_engine.chat import build_ainv_facts_pack`) to keep the `service ↔ chat` import acyclic. Any test that patches it MUST patch the **chat-module** path (`...ainv_engine.chat.build_ainv_facts_pack`), never a service-module attribute.
- **`persist` gate:** persist only when `persist and chat_session_id is not None` — counterfactual / no-session paths skip the write, exactly like `compute_rebalancing_result`.

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (Plan 3a Task 4) — promote the persist imports (the additional-investment persist service + `persist_practical_allocation_run`) to module scope, and replace the 3a `if persist:` block so it gates on a live chat session, persists the practical run inline to capture `source_allocation_run_id`, then persists the additional-investment run.
- Modify: `app/domains/additional_investment/services/ainv_engine/chat.py` (Plan 3a) — add `persist=True` to the first-turn compute call (3a omits it, so it defaults `False`); surface `additional_investment_run_id=outcome.run_id` on the success `ChatHandlerResult`.
- Modify: `app/domains/additional_investment/services/additional_investment_module_service.py` (Plan 3a) — set `persisted_run_id=result.additional_investment_run_id` on the returned `ModuleOutput`.
- Modify: `app/domains/ai_engine/chat_dispatcher.py:32` — add `additional_investment_run_id: uuid.UUID | None = None` to `ChatHandlerResult`.
- Create + Test: `app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py` (`tests/__init__.py` already exists from Plan 3a).

**Interfaces:**
- **Consumes:** `persist_additional_investment_recommendation(db, user_id, output, *, source_allocation_run_id, chat_session_id=None, used_cached_allocation=False, user_question=None, request=None) -> uuid.UUID` (Plan 3b earlier task); `persist_practical_allocation_run(db, *, user_id, output, chat_session_id=None, user_question=None, input_payload=None) -> uuid.UUID` (`app/domains/practical_asset_allocation/services/practical_allocation_persist_service.py`); `compute_additional_investment_result` + `AdditionalInvestmentRunOutcome` (Plan 3a Task 4); `build_additional_investment_input_for_user`, `build_ainv_facts_pack` (Plan 3a); `compute_practical_allocation_result` (existing); `run_additional_investment` / `Cadence` / `AdditionalInvestmentOutput` (AI_Agents); `ChatHandlerResult` / `register` / `dispatch_chat` / `ModuleOutput`.
- **Produces:**
  - `compute_additional_investment_result(user, user_question, *, db, acting_user_id, chat_session_id, deploy_amount_inr: float, cadence: Cadence, chat_ctx: TurnContext, persist: bool = False) -> AdditionalInvestmentRunOutcome` — signature unchanged from Plan 3a (`chat_ctx` is a required keyword; `persist` still defaults `False`). The chat handler now calls it with `persist=True`, so it persists once and sets `outcome.run_id` when `persist and chat_session_id is not None`.
  - The practical allocation run is persisted inline (via `persist_practical_allocation_run`) to obtain a non-null `source_allocation_run_id`; no separate provenance-lookup helper is added (Option B replaces the Option-A `_latest_asset_allocation_run_id` lookup).
  - `ChatHandlerResult.additional_investment_run_id: uuid.UUID | None`.
  - `additional_investment_module_service.run(...)` now emits `ModuleOutput.persisted_run_id`.

---

- [ ] **Step 1: Write the failing test**

`app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py`:
```python
"""Plan 3b Task 4 — persistence wiring in the additional-investment engine service.

Pure-unit: every collaborator (practical allocation + its inline persist that
yields source_allocation_run_id, input builder, engine, persist service) is
stubbed at the service-module namespace,
and ``build_ainv_facts_pack`` at its CHAT-module path (the service imports it
lazily from chat.py — D6), so no DB / LLM / AI_Agents engine actually runs. The
awaited input builder is patched with ``AsyncMock``. We assert only the
persist hand-off — ``persist=True`` + a chat session ⇒ persist called exactly
once with the engine output and the resolved source-allocation id, and the
returned run id flows onto ``AdditionalInvestmentRunOutcome.run_id``;
``persist=False`` skips the write and leaves ``run_id`` ``None``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

_SVC = "app.domains.additional_investment.services.ainv_engine.service"


def _fake_output():
    """Minimal valid AdditionalInvestmentOutput (empty buys/targets is allowed)."""
    from additional_investment.models import (  # type: ignore[import-not-found]
        AdditionalInvestmentOutput,
        TargetBucket,
        Cadence,
    )

    return AdditionalInvestmentOutput(
        target_bucket=TargetBucket.LONG_TERM,
        cadence=Cadence.LUMPSUM,
        deploy_amount_inr=400000.0,
        deployed_inr=400000.0,
        undeployed_inr=0.0,
        per_subgroup_target=[],
        buys=[],
    )


@pytest.mark.asyncio
async def test_persist_true_calls_persist_once_and_returns_run_id():
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    run_id = uuid.uuid4()
    source_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(result=MagicMock(), blocking_message=None)

    with (
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=source_id),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {"debug": "x"})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(return_value=run_id),
        ) as persist_mock,
    ):
        outcome = await compute_additional_investment_result(
            user=user,
            user_question="invest 4 lakh lumpsum",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=session_id,
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=True,
            chat_ctx=MagicMock(),
        )

    persist_mock.assert_awaited_once()
    args, kwargs = persist_mock.await_args
    # positional: (db, acting_user_id, output)
    assert args[2] is output
    assert kwargs["source_allocation_run_id"] == source_id
    assert kwargs["chat_session_id"] == session_id
    assert kwargs["used_cached_allocation"] is False
    assert kwargs["user_question"] == "invest 4 lakh lumpsum"

    assert outcome.run_id == run_id
    assert outcome.output is output
    assert outcome.blocking_message is None


@pytest.mark.asyncio
async def test_persist_false_skips_persist_and_run_id_is_none():
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(result=MagicMock(), blocking_message=None)

    with (
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(return_value=uuid.uuid4()),
        ) as persist_mock,
    ):
        outcome = await compute_additional_investment_result(
            user=user,
            user_question="what if I invested 4 lakh",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=uuid.uuid4(),
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=False,
            chat_ctx=MagicMock(),
        )

    persist_mock.assert_not_awaited()
    assert outcome.run_id is None
    assert outcome.output is output


@pytest.mark.asyncio
async def test_first_time_deploy_persists_practical_run_for_source_id():
    """First-time deploy (no prior goal allocation): source_allocation_run_id is
    still non-null because the practical run is persisted inline (Option B) and its
    id is forwarded to the additional-investment persist — there is no nullable
    fallback lookup that could return None."""
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    practical_run_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(result=MagicMock(), blocking_message=None)

    with (
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=practical_run_id),
        ) as practical_persist_mock,
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(return_value=uuid.uuid4()),
        ) as persist_mock,
    ):
        await compute_additional_investment_result(
            user=user,
            user_question="invest 4 lakh lumpsum",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=session_id,
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=True,
            chat_ctx=MagicMock(),
        )

    # The practical run is persisted inline, and its fresh, non-null id is the
    # source_allocation_run_id handed to the additional-investment persist.
    practical_persist_mock.assert_awaited_once()
    persist_mock.assert_awaited_once()
    _, kwargs = persist_mock.await_args
    assert kwargs["source_allocation_run_id"] == practical_run_id
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`):
```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py -v
```
Expected: FAIL at patch setup — the 3a `service.py` has neither `persist_practical_allocation_run` nor `persist_additional_investment_recommendation` in its namespace, so `patch(...)` raises
`AttributeError: <module '...ainv_engine.service'> does not have the attribute 'persist_additional_investment_recommendation'`.

- [ ] **Step 3: Write minimal implementation (exact diff hunks against the 3a version)**

**(a) `app/domains/additional_investment/services/ainv_engine/service.py`**

3a baseline (relevant excerpts — the REAL Plan 3a Task 4 output): the import block and the tail of `compute_additional_investment_result`. The 3a code ALREADY has an `if persist:` block (persist defaults `False`, so it is never entered in 3a) that lazily imports the persist service and passes `source_allocation_run_id=None`:
```python
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

from app.domains.ai_engine.common import ensure_ai_agents_path, trace_line
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    build_additional_investment_input_for_user,
)
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    compute_practical_allocation_result,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
)
from additional_investment.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    run_additional_investment,
)
```
```python
    response: AdditionalInvestmentOutput = await asyncio.to_thread(
        run_additional_investment,
        inp,
    )

    # ``build_ainv_facts_pack`` lives in chat.py; import it lazily (D6).
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts_pack = build_ainv_facts_pack(response)

    run_id: Optional[uuid.UUID] = None
    if persist:
        from app.domains.additional_investment.services.additional_investment_persist_service import (
            persist_additional_investment_recommendation,
        )

        run_id = await persist_additional_investment_recommendation(
            db,
            acting_user_id,
            response,
            source_allocation_run_id=None,
            chat_session_id=chat_session_id,
            used_cached_allocation=False,
            user_question=user_question,
        )

    return AdditionalInvestmentRunOutcome(
        output=response,
        facts_pack=facts_pack,
        run_id=run_id,
        used_cached_allocation=False,
    )
```
(The engine-output variable is `response`; the facts pack comes from the lazy chat-path import (D6); `compute_additional_investment_result` already takes `chat_ctx: TurnContext` and `persist: bool = False` per the Plan 3a signature.)

Diff hunk 1 — imports (promote both persist services to module scope; Option B needs no SQLAlchemy lookup helpers, AA-run model, or portfolio helper):
```diff
 from sqlalchemy.ext.asyncio import AsyncSession

 if TYPE_CHECKING:
     from app.domains.ai_engine.turn_context import TurnContext

 from app.domains.ai_engine.common import ensure_ai_agents_path, trace_line
 from app.domains.additional_investment.services.ainv_engine.input_builder import (
     build_additional_investment_input_for_user,
 )
 from app.domains.practical_asset_allocation.services.paa_engine.service import (
     compute_practical_allocation_result,
 )
+from app.domains.practical_asset_allocation.services.practical_allocation_persist_service import (
+    persist_practical_allocation_run,
+)
+from app.domains.additional_investment.services.additional_investment_persist_service import (
+    persist_additional_investment_recommendation,
+)
```

Diff hunk 2 — remove the Option-A provenance helper. Under Option B no `_latest_asset_allocation_run_id` lookup is added: the practical run is persisted inline in the persist block (hunk 3 below), which always yields a non-null `source_allocation_run_id`. (Nothing to add here.)

Diff hunk 3 — replace the 3a `if persist:` block: drop the lazy persist import (now module-scoped via hunk 1), gate on a live chat session, and resolve the real `source_allocation_run_id` by persisting the practical run inline (Option B) instead of `None`. `paa_outcome` is the `PracticalAllocationRunOutcome` already captured near the top of the function from `compute_practical_allocation_result(...)`; `paa_outcome.result` is the `PracticalAllocationOutput` the deploy input was built from. The engine-output variable stays `response` and `used_cached_allocation=False` (the real 3a names):
```diff
     facts_pack = build_ainv_facts_pack(response)

-    run_id: Optional[uuid.UUID] = None
-    if persist:
-        from app.domains.additional_investment.services.additional_investment_persist_service import (
-            persist_additional_investment_recommendation,
-        )
-
-        run_id = await persist_additional_investment_recommendation(
-            db,
-            acting_user_id,
-            response,
-            source_allocation_run_id=None,
-            chat_session_id=chat_session_id,
-            used_cached_allocation=False,
-            user_question=user_question,
-        )
+    # Persist the BUY-only recommendation. Gated like compute_rebalancing_result:
+    # counterfactual / no-session paths (persist=False or no chat session) skip
+    # the write. Money stays float — persist writes Numeric(18,2) directly.
+    #
+    # source_allocation_run_id (Option B): the practical allocation run the deploy
+    # is derived from. compute_practical_allocation_result returns no run id, so we
+    # persist the practical run inline here to capture it — the only practical
+    # persist in the ainv path (it does not route through paa_engine/chat.py), so
+    # no double-write. The id is always produced, so the FK column is NOT NULL.
+    run_id: uuid.UUID | None = None
+    if persist and chat_session_id is not None:
+        source_allocation_run_id = await persist_practical_allocation_run(
+            db,
+            user_id=acting_user_id,
+            output=paa_outcome.result,
+            chat_session_id=chat_session_id,
+            user_question=user_question,
+        )
+        run_id = await persist_additional_investment_recommendation(
+            db,
+            acting_user_id,
+            response,
+            source_allocation_run_id=source_allocation_run_id,
+            chat_session_id=chat_session_id,
+            used_cached_allocation=False,
+            user_question=user_question,
+            request=inp,
+        )

     return AdditionalInvestmentRunOutcome(
         output=response,
         facts_pack=facts_pack,
         run_id=run_id,
         used_cached_allocation=False,
     )
```

**(b) `app/domains/ai_engine/chat_dispatcher.py` — add the run-id field to `ChatHandlerResult` (after line 32):**
```diff
     rebalancing_run_id: uuid.UUID | None = None
     rebalancing_response: Any | None = None
+    additional_investment_run_id: uuid.UUID | None = None
     chart_payloads: list[dict[str, Any]] | None = None
```

**(c) `app/domains/additional_investment/services/ainv_engine/chat.py` — pass `persist=True` on the compute call and surface the run id on the success result.**

3a baseline (first-turn region of `handle` — note the positional `ctx.user_ctx`/`ctx.user_question`, the required `chat_ctx=ctx`, the truthy `if outcome.blocking_message:` relayed via `format_relay_or_canned`, and the `_format_or_fallback_ainv(ctx, outcome.output)` call form; `persist` is NOT passed in 3a, so it defaults `False`):
```python
    outcome = await compute_additional_investment_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        deploy_amount_inr=amount,
        cadence=cadence,
        chat_ctx=ctx,
    )
    if outcome.blocking_message:
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=outcome.blocking_message,
        )
        return ChatHandlerResult(text=text)

    text = await _format_or_fallback_ainv(ctx, outcome.output)
    return ChatHandlerResult(text=text)
```

Diff hunk:
```diff
     outcome = await compute_additional_investment_result(
         ctx.user_ctx,
         ctx.user_question,
         db=ctx.db,
         acting_user_id=ctx.effective_user_id,
         chat_session_id=ctx.session_id,
         deploy_amount_inr=amount,
         cadence=cadence,
         chat_ctx=ctx,
+        persist=True,  # Plan 3b: persist the recommendation row
     )
     if outcome.blocking_message:
         text = await format_relay_or_canned(
             ctx=ctx,
             module_name="additional_investment",
             message=outcome.blocking_message,
         )
         return ChatHandlerResult(text=text)

     text = await _format_or_fallback_ainv(ctx, outcome.output)
-    return ChatHandlerResult(text=text)
+    return ChatHandlerResult(
+        text=text,
+        additional_investment_run_id=outcome.run_id,
+    )
```

**(d) `app/domains/additional_investment/services/additional_investment_module_service.py` — map the run id onto `ModuleOutput.persisted_run_id`.**

3a baseline (the REAL mapping already sets `persisted_run_id=None`; `snapshot_id` was removed from the mapping by 3a's Finding-6 fix, and `chart_payloads` is a not-yet-populated forward hook):
```python
    result = await dispatch_chat("additional_investment", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,  # the structured additional-investment chat result for the HTTP layer
        persisted_run_id=None,  # 3a: AdditionalInvestmentRun persistence lands in 3b
        chart_payloads=result.chart_payloads,  # not-yet-populated forward hook
    )
```

Diff hunk:
```diff
     result = await dispatch_chat("additional_investment", ctx)
     return ModuleOutput(
         text=result.text,
         payload=result,  # the structured additional-investment chat result for the HTTP layer
-        persisted_run_id=None,  # 3a: AdditionalInvestmentRun persistence lands in 3b
+        persisted_run_id=result.additional_investment_run_id,  # Plan 3b: the persisted run id
         chart_payloads=result.chart_payloads,  # not-yet-populated forward hook
     )
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`):
```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py -v
```
Expected: PASS (3 passed) — `test_persist_true_calls_persist_once_and_returns_run_id`, `test_persist_false_skips_persist_and_run_id_is_none`, and `test_first_time_deploy_persists_practical_run_for_source_id`.

- [ ] **Step 5: Commit**

```bash
git add app/domains/additional_investment/services/ainv_engine/service.py \
        app/domains/additional_investment/services/ainv_engine/chat.py \
        app/domains/additional_investment/services/additional_investment_module_service.py \
        app/domains/ai_engine/chat_dispatcher.py \
        app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py
git commit -m "feat(additional_investment): persist recommendation run + surface run id"
```

## Deferred — read/serve side (build when the Invest-page UI is designed)

> Reason: frontend rendering is TBD — the chat reply already names the funds, and the persisted run id is surfaced via `ModuleOutput.persisted_run_id`. Build the two tasks below (the run-detail / display schema with the Invest-page summary headline, and the read router) once the Invest-page UI is designed. They are NOT part of the active storage-only scope (Tasks 1–4).

### Task 5 (deferred): Detail schema + Invest-page headline (additional_investment)

Mirrors the rebalancing domain's `RebalancingRunDetailResponse.summary` + `rebalancing_summary.py` + its unit test, for the new `additional_investment` app domain. Adds the read-time `AdditionalInvestmentRunDetailResponse` (pydantic view over the 3b-T3 ORM run + children) and its `summary` computed field, backed by a NEW pure, deterministic Invest-page headline builder. This is a deliberate exception to the shared chat-formatter direction (same as the rebalancing headline): the Invest-page header is a backend computed-field, not LLM-formatted.

**Decided defaults (state in this task):**
- Money is `float` (allocation family), NOT `Decimal` — every `*_inr` field is plain `float`. Do NOT import `_to_decimal`.
- I/O naming = Input/Output family; `target_bucket` and `cadence` are exposed as `str` (the engine enums' `.value`), matching how the persist layer stores them.
- The headline builder is pure + duck-typed (no DB, no LLM) — anything exposing `buys`, `deployed_inr`, `undeployed_inr`, `deploy_amount_inr`, `cadence` works, so it is unit-tested with plain stand-ins.

**Files:**
- Create: `app/domains/additional_investment/services/additional_investment_summary.py` — `build_additional_investment_summary(run)` + `AdditionalInvestmentSummary` dataclass.
- Create: `app/domains/additional_investment/schemas/__init__.py` — `AdditionalInvestmentTargetSchema`, `AdditionalInvestmentBuySchema`, `AdditionalInvestmentSummarySchema`, `AdditionalInvestmentRunDetailResponse`.
- Create (empty markers, only if a prior 3b task has not already): `app/domains/additional_investment/__init__.py`, `app/domains/additional_investment/services/__init__.py`, `app/domains/additional_investment/services/tests/__init__.py`.
- Test: `app/domains/additional_investment/services/tests/test_additional_investment_summary.py` — covers the builder (None when no buys, lumpsum/SIP/under-deployed headlines) AND the schema's read-time `summary` computed field (validates from a duck-typed ORM run; `None` when empty).

**Interfaces:**
- Consumes: `format_inr_indian` from `app/domains/ai_engine/common.py` (existing); the 3b-T3 ORM `AdditionalInvestmentRun` + children supply the runtime attributes the schema reads (tests use stand-ins, so no ORM import here); engine I/O field names from the FIXED INTERFACE CONTRACT (`AdditionalInvestmentOutput`, `SubgroupTarget`, `FundBuy` in `AI_Agents/src/additional_investment/models.py`).
- Produces (later tasks rely on these exact signatures):
  - `build_additional_investment_summary(run) -> AdditionalInvestmentSummary | None`
  - `@dataclass(frozen=True) AdditionalInvestmentSummary(title: str, subtitle: str, reason: str | None = None)`
  - `AdditionalInvestmentSummarySchema(title: str, subtitle: str, reason: Optional[str] = None)`
  - `AdditionalInvestmentTargetSchema` / `AdditionalInvestmentBuySchema` (both `ConfigDict(from_attributes=True)`)
  - `AdditionalInvestmentRunDetailResponse` (`ConfigDict(from_attributes=True)`) with `per_subgroup_target: List[AdditionalInvestmentTargetSchema] = []`, `buys: List[AdditionalInvestmentBuySchema] = []`, and `@computed_field` property `summary -> Optional[AdditionalInvestmentSummarySchema]` (consumed by the 3b router that returns the run detail).

---

- [ ] **Step 1: Write the failing test**

  Create `app/domains/additional_investment/services/tests/test_additional_investment_summary.py`:

  ```python
  """Unit tests for the additional-investment Invest-page headline builder and the
  read-time `summary` computed field on the run-detail schema.

  The builder is a pure function over a run's deploy accounting + BUY list, so these
  tests use lightweight stand-ins (no DB, no ORM, no LLM) that expose only the
  attributes the builder reads. The schema test validates the response model from a
  duck-typed "ORM run" the same way the run-detail router would.
  """

  import uuid
  from datetime import datetime
  from types import SimpleNamespace

  from app.domains.additional_investment.schemas import (
      AdditionalInvestmentRunDetailResponse,
  )
  from app.domains.additional_investment.services.additional_investment_summary import (
      build_additional_investment_summary,
  )


  def _buy(**kw):
      base = dict(
          id=uuid.uuid4(),
          recommended_fund="Fund A",
          isin="INF000A",
          sub_category="Flexi Cap",
          asset_subgroup="flexi_cap",
          amount_inr=100_000.0,
          monthly_amount_inr=None,
          reason="rank-1 pick",
      )
      base.update(kw)
      return SimpleNamespace(**base)


  def _run(**kw):
      base = dict(
          deploy_amount_inr=0.0,
          deployed_inr=0.0,
          undeployed_inr=0.0,
          cadence="lumpsum",
          buys=[],
      )
      base.update(kw)
      return SimpleNamespace(**base)


  # ── builder ──────────────────────────────────────────────────────────


  def test_returns_none_when_run_missing():
      assert build_additional_investment_summary(None) is None


  def test_returns_none_when_no_buys():
      assert build_additional_investment_summary(_run(buys=[])) is None


  def test_lumpsum_headline_names_amount_and_count():
      run = _run(
          deploy_amount_inr=500_000.0,
          deployed_inr=500_000.0,
          undeployed_inr=0.0,
          cadence="lumpsum",
          buys=[_buy(), _buy(), _buy()],
      )
      s = build_additional_investment_summary(run)
      assert s.title == "Deploying ₹5 lakh across 3 funds"
      assert "recommended picks" in s.subtitle
      assert s.reason is None


  def test_single_fund_is_singular():
      run = _run(
          deploy_amount_inr=100_000.0,
          deployed_inr=100_000.0,
          cadence="lumpsum",
          buys=[_buy()],
      )
      s = build_additional_investment_summary(run)
      assert s.title == "Deploying ₹1 lakh across 1 fund"


  def test_sip_headline_uses_monthly_framing():
      run = _run(
          deploy_amount_inr=25_000.0,
          deployed_inr=25_000.0,
          undeployed_inr=0.0,
          cadence="sip_monthly",
          buys=[_buy(monthly_amount_inr=15_000.0), _buy(monthly_amount_inr=10_000.0)],
      )
      s = build_additional_investment_summary(run)
      assert s.title == "Starting a ₹25,000/month SIP"
      assert "2 recommended funds" in s.subtitle
      assert s.reason is None


  def test_under_deployed_nudge_leads_when_remainder_is_significant():
      run = _run(
          deploy_amount_inr=1_000_000.0,
          deployed_inr=850_000.0,
          undeployed_inr=150_000.0,  # 15% couldn't be placed → nudge dominates
          cadence="lumpsum",
          buys=[_buy(), _buy(), _buy(), _buy()],
      )
      s = build_additional_investment_summary(run)
      assert s.title == "Investing ₹8.5 lakh of ₹10 lakh"
      assert "₹1.5 lakh is left over" in s.subtitle
      assert (
          s.reason
          == "Per-fund caps and the funds available capped how much fits right now."
      )


  def test_tiny_remainder_does_not_trigger_nudge():
      # ₹100 rounding crumbs (< 5% of the deploy amount) keep the clean lumpsum headline.
      run = _run(
          deploy_amount_inr=500_000.0,
          deployed_inr=499_900.0,
          undeployed_inr=100.0,
          cadence="lumpsum",
          buys=[_buy(), _buy()],
      )
      s = build_additional_investment_summary(run)
      assert s.title.startswith("Deploying")
      assert "left over" not in s.subtitle


  # ── schema computed field ────────────────────────────────────────────


  def _orm_run(**kw):
      base = dict(
          id=uuid.uuid4(),
          user_id=uuid.uuid4(),
          portfolio_id=uuid.uuid4(),
          chat_session_id=None,
          source_allocation_run_id=uuid.uuid4(),
          engine_version="ainv-1",
          target_bucket="long_term",
          cadence="lumpsum",
          deploy_amount_inr=500_000.0,
          deployed_inr=500_000.0,
          undeployed_inr=0.0,
          user_question="invest 5 lakh",
          created_at=datetime(2026, 6, 27, 12, 0, 0),
          updated_at=datetime(2026, 6, 27, 12, 0, 0),
          per_subgroup_target=[
              SimpleNamespace(
                  id=uuid.uuid4(),
                  subgroup="flexi_cap",
                  ratio=0.6,
                  target_inr=300_000.0,
              )
          ],
          buys=[
              SimpleNamespace(
                  id=uuid.uuid4(),
                  recommended_fund="Fund A",
                  isin="INF000A",
                  sub_category="Flexi Cap",
                  asset_subgroup="flexi_cap",
                  amount_inr=300_000.0,
                  monthly_amount_inr=None,
                  reason="rank-1 pick",
              ),
              SimpleNamespace(
                  id=uuid.uuid4(),
                  recommended_fund="Fund B",
                  isin="INF000B",
                  sub_category="Large Cap",
                  asset_subgroup="large_cap",
                  amount_inr=200_000.0,
                  monthly_amount_inr=None,
                  reason="rank-1 pick",
              ),
          ],
      )
      base.update(kw)
      return SimpleNamespace(**base)


  def test_schema_validates_from_orm_run_and_exposes_headline():
      resp = AdditionalInvestmentRunDetailResponse.model_validate(_orm_run())
      assert resp.target_bucket == "long_term"
      assert resp.cadence == "lumpsum"
      assert len(resp.per_subgroup_target) == 1
      assert resp.per_subgroup_target[0].subgroup == "flexi_cap"
      assert len(resp.buys) == 2
      assert resp.buys[0].recommended_fund == "Fund A"
      assert resp.summary is not None
      assert resp.summary.title == "Deploying ₹5 lakh across 2 funds"


  def test_schema_summary_is_none_when_no_buys():
      resp = AdditionalInvestmentRunDetailResponse.model_validate(
          _orm_run(buys=[], deployed_inr=0.0, undeployed_inr=0.0)
      )
      assert resp.summary is None
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  .venv-mac/bin/python -m pytest app/domains/additional_investment/services/tests/test_additional_investment_summary.py -v
  ```

  Expected failure: collection error — `ModuleNotFoundError: No module named 'app.domains.additional_investment.schemas'` (and `...services.additional_investment_summary`), because neither the schema package nor the builder module exists yet.

- [ ] **Step 3: Write minimal implementation**

  First ensure the empty package markers exist (idempotent — skip any a prior 3b task already created). Create each missing file with empty content:
  - `app/domains/additional_investment/__init__.py`
  - `app/domains/additional_investment/services/__init__.py`
  - `app/domains/additional_investment/services/tests/__init__.py`

  Create `app/domains/additional_investment/services/additional_investment_summary.py`:

  ```python
  """Build a plan-aware Invest-page headline for an additional-investment run.

  Pure and deterministic — no DB, no LLM. Reads the run's deploy accounting
  (``deployed_inr`` / ``undeployed_inr`` / ``deploy_amount_inr`` / ``cadence``) plus
  the BUY list and picks the ONE dominant story to lead with, so the Invest page
  shows copy that reflects what this deploy actually does instead of a static header.

  Mirrors ``rebalancing_summary.build_rebalance_summary``. Consumed by
  ``AdditionalInvestmentRunDetailResponse.summary`` (a computed field). The input is
  duck-typed: anything exposing the documented attributes works, which keeps the
  function trivially unit-testable with plain stand-ins.
  """

  from __future__ import annotations

  from dataclasses import dataclass

  from app.domains.ai_engine.common import format_inr_indian

  # A run is "under-deployed enough to lead with" once at least this share of the
  # deploy amount couldn't be placed (per-fund caps / fund scarcity). Below it, the
  # ₹100-rounding crumbs are ignored and the clean deploy headline leads instead.
  _UNDEPLOYED_LEAD_FRACTION = 0.05


  @dataclass(frozen=True)
  class AdditionalInvestmentSummary:
      title: str
      subtitle: str
      reason: str | None = None  # one-line "why", or None when nothing needs justifying


  def _fund_noun(n: int) -> str:
      return "fund" if n == 1 else "funds"


  def build_additional_investment_summary(run) -> AdditionalInvestmentSummary | None:
      """Return the Invest-page headline for a run, or ``None`` when there is nothing to deploy.

      ``run`` is duck-typed: it must expose ``buys`` (a sequence), ``deployed_inr``,
      ``undeployed_inr``, ``deploy_amount_inr``, and ``cadence`` (the ``Cadence`` value
      as a string, e.g. ``"sip_monthly"``). Returns ``None`` when the run is missing or
      has no buys.
      """
      if run is None:
          return None

      buys = list(getattr(run, "buys", ()) or ())
      if not buys:
          return None

      n = len(buys)
      noun = _fund_noun(n)
      deployed = float(getattr(run, "deployed_inr", 0) or 0)
      undeployed = float(getattr(run, "undeployed_inr", 0) or 0)
      deploy_amount = float(getattr(run, "deploy_amount_inr", 0) or 0)
      is_sip = str(getattr(run, "cadence", "") or "") == "sip_monthly"

      # 1 — A meaningful unplaced remainder dominates the story: name what actually
      # went in and nudge that caps / fund availability held the rest back.
      if (
          undeployed > 0
          and deploy_amount > 0
          and undeployed / deploy_amount >= _UNDEPLOYED_LEAD_FRACTION
      ):
          return AdditionalInvestmentSummary(
              title=(
                  f"Investing {format_inr_indian(deployed)} "
                  f"of {format_inr_indian(deploy_amount)}"
              ),
              subtitle=(
                  f"Spread across {n} {noun}; "
                  f"{format_inr_indian(undeployed)} is left over."
              ),
              reason="Per-fund caps and the funds available capped how much fits right now.",
          )

      # 2 — A monthly SIP: frame the per-month amount, not a one-off deploy.
      if is_sip:
          return AdditionalInvestmentSummary(
              title=f"Starting a {format_inr_indian(deployed)}/month SIP",
              subtitle=f"Split across {n} recommended {noun} to match your target mix.",
          )

      # 3 — A clean lumpsum deploy: name the amount and how many funds it buys.
      return AdditionalInvestmentSummary(
          title=f"Deploying {format_inr_indian(deployed)} across {n} {noun}",
          subtitle="Buying into recommended picks to match your target mix.",
      )
  ```

  Create `app/domains/additional_investment/schemas/__init__.py`:

  ```python
  """Pydantic response schemas for the additional-investment endpoints."""

  from __future__ import annotations

  import uuid
  from datetime import datetime
  from typing import List, Optional

  from pydantic import BaseModel, ConfigDict, computed_field

  from app.domains.additional_investment.services.additional_investment_summary import (
      build_additional_investment_summary,
  )


  # ── Nested child schemas ────────────────────────────────────────────────


  class AdditionalInvestmentTargetSchema(BaseModel):
      model_config = ConfigDict(from_attributes=True)

      id: uuid.UUID
      subgroup: str
      ratio: float
      target_inr: float


  class AdditionalInvestmentBuySchema(BaseModel):
      model_config = ConfigDict(from_attributes=True)

      id: uuid.UUID
      recommended_fund: str
      isin: str
      sub_category: str
      asset_subgroup: str
      amount_inr: float
      monthly_amount_inr: Optional[float] = None
      reason: str


  class AdditionalInvestmentSummarySchema(BaseModel):
      """Plan-aware headline (title + one-line subtitle) for an additional-investment run.

      Replaces the Invest page's static deploy header with copy that reflects what
      this run actually buys. Computed on read from the run's deploy accounting + BUY
      list — see ``additional_investment_summary``.
      """

      title: str  # what we're doing
      subtitle: str  # how / the numbers
      reason: Optional[str] = None  # one-line why; None when nothing needs justifying


  # ── Top-level response schema ───────────────────────────────────────────


  class AdditionalInvestmentRunDetailResponse(BaseModel):
      """Full detail with eager-loaded per-subgroup targets and buys."""

      model_config = ConfigDict(from_attributes=True)

      id: uuid.UUID
      user_id: uuid.UUID
      portfolio_id: uuid.UUID
      chat_session_id: Optional[uuid.UUID] = None
      source_allocation_run_id: uuid.UUID

      engine_version: str

      target_bucket: str
      cadence: str
      deploy_amount_inr: float
      deployed_inr: float
      undeployed_inr: float

      user_question: Optional[str] = None

      created_at: datetime
      updated_at: datetime

      per_subgroup_target: List[AdditionalInvestmentTargetSchema] = []
      buys: List[AdditionalInvestmentBuySchema] = []

      @computed_field  # type: ignore[prop-decorator]
      @property
      def summary(self) -> Optional[AdditionalInvestmentSummarySchema]:
          """Personalized Invest-page headline derived from this run's deploy accounting + buys."""
          result = build_additional_investment_summary(self)
          if result is None:
              return None
          return AdditionalInvestmentSummarySchema(
              title=result.title, subtitle=result.subtitle, reason=result.reason
          )


  __all__ = [
      "AdditionalInvestmentTargetSchema",
      "AdditionalInvestmentBuySchema",
      "AdditionalInvestmentSummarySchema",
      "AdditionalInvestmentRunDetailResponse",
  ]
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  .venv-mac/bin/python -m pytest app/domains/additional_investment/services/tests/test_additional_investment_summary.py -v
  ```

  Expected: PASS — all 9 tests green (`test_returns_none_when_run_missing`, `test_returns_none_when_no_buys`, `test_lumpsum_headline_names_amount_and_count`, `test_single_fund_is_singular`, `test_sip_headline_uses_monthly_framing`, `test_under_deployed_nudge_leads_when_remainder_is_significant`, `test_tiny_remainder_does_not_trigger_nudge`, `test_schema_validates_from_orm_run_and_exposes_headline`, `test_schema_summary_is_none_when_no_buys`).

- [ ] **Step 5: Commit**

  ```bash
  git -C /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend add \
    app/domains/additional_investment/__init__.py \
    app/domains/additional_investment/services/__init__.py \
    app/domains/additional_investment/services/additional_investment_summary.py \
    app/domains/additional_investment/services/tests/__init__.py \
    app/domains/additional_investment/services/tests/test_additional_investment_summary.py \
    app/domains/additional_investment/schemas/__init__.py

  git -C /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend commit -m "$(cat <<'EOF'
  feat(additional_investment): run-detail schema + Invest-page headline

  Add AdditionalInvestmentRunDetailResponse (+ target/buy/summary child schemas)
  with a read-time `summary` computed field, backed by a new pure, deterministic
  build_additional_investment_summary() headline builder. Mirrors the rebalancing
  RebalancingRunDetailResponse.summary + rebalancing_summary pattern. Money is
  float (allocation family), target_bucket/cadence exposed as str.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  EOF
  )"
  ```

### Task 6 (deferred): Router + read path (additional-investment read surface)

Mirrors `app/domains/rebalancing/routers/rebalancing_router.py`, minus the status/readiness routes. Additional-investment runs are BUY-only and write-once (DECIDED DEFAULT: no status-lifecycle enum), so there is **no** `PUT /{run_id}/status` route. Two read endpoints only: `GET /` (light list) and `GET /{run_id}` (eager-loaded detail incl. the read-time `summary` headline). Auth via `get_effective_user`; unknown id → 404.

**Files:**
- **Create** `app/domains/additional_investment/routers/additional_investment_router.py` — the `/additional-investment` router (list + detail).
- **Create** `app/domains/additional_investment/routers/__init__.py` — exports `router`.
- **Create** `app/domains/additional_investment/routers/tests/__init__.py` — empty package marker (mirrors `app/domains/rebalancing/routers/` having an `__init__.py`).
- **Modify** `app/domains/additional_investment/schemas/__init__.py` (file created by the 3b detail-schema task) — add `AdditionalInvestmentRunListItem` and append its name to `__all__`.
- **Modify** `app/routers/__init__.py:20` (import block, right after the `rebalancing_router` import at lines 20–22) and `app/routers/__init__.py:47` (the `all_routers` list, right after `rebalancing_router,`).
- **Test** `app/domains/additional_investment/routers/tests/test_router_read_path.py`.

**Interfaces:**
- **Consumes:**
  - `AdditionalInvestmentRun` (+ relationships `.targets`, `.buys`) — `app.domains.additional_investment.models` (3b ORM task).
  - `AdditionalInvestmentRunDetailResponse` (with computed `summary`), `AdditionalInvestmentTargetSchema`, `AdditionalInvestmentBuySchema`, `AdditionalInvestmentSummarySchema` — `app.domains.additional_investment.schemas` (3b detail-schema task).
  - `build_ainv_summary` — invoked indirectly through the detail schema's `summary` computed_field (3b summary task).
  - `from app.core.database import get_db`; `from app.core.dependencies import CurrentUser, get_effective_user`.
- **Produces:**
  - `router = APIRouter(prefix="/additional-investment", tags=["Additional Investment"])`.
  - `GET "/" -> list[AdditionalInvestmentRunListItem]`; `GET "/{run_id}" -> AdditionalInvestmentRunDetailResponse` (404 `detail="Additional investment run not found"`).
  - `AdditionalInvestmentRunListItem` schema (light listing row).
  - `additional_investment_router` registered in `app/routers/__init__.py` `all_routers`.

---

- [ ] **Step 1: Write the failing test** — create `app/domains/additional_investment/routers/tests/test_router_read_path.py`:

```python
"""Read-path test for the additional-investment router (no real DB / LLM).

Mirrors the rebalancing router's GET /{run_id} contract: 200 returns the
persisted run detail incl the computed ``summary``; an unknown id -> 404. The
route is driven through a minimal FastAPI app with ``get_db`` /
``get_effective_user`` overridden by plain fakes, so the test exercises the
router + detail schema without a database or network.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.additional_investment.routers import router

_USER_ID = uuid.uuid4()


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj

    def scalars(self):
        rows = [] if self._obj is None else [self._obj]
        return SimpleNamespace(all=lambda: rows)


class _FakeSession:
    """Stands in for AsyncSession: every execute() returns the same stubbed row."""

    def __init__(self, row):
        self._row = row

    async def execute(self, _stmt):
        return _FakeResult(self._row)


def _stub_run(run_id):
    """A detached, DB-free stand-in shaped like AdditionalInvestmentRun + children."""
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=run_id,
        user_id=_USER_ID,
        source_allocation_run_id=uuid.uuid4(),
        chat_session_id=None,
        target_bucket="long_term",
        cadence="lumpsum",
        deploy_amount_inr=100000.0,
        deployed_inr=100000.0,
        undeployed_inr=0.0,
        engine_version="ainv-1.0",
        user_question="I have 1 lakh to invest",
        created_at=now,
        updated_at=now,
        targets=[
            SimpleNamespace(
                id=uuid.uuid4(),
                subgroup="large_cap",
                ratio=1.0,
                target_inr=100000.0,
            )
        ],
        buys=[
            SimpleNamespace(
                id=uuid.uuid4(),
                recommended_fund="ABC Bluechip Fund",
                isin="INF000A01ABC",
                sub_category="large_cap",
                asset_subgroup="large_cap",
                amount_inr=100000.0,
                monthly_amount_inr=None,
                reason="rank-1 fund for large_cap",
            )
        ],
    )


def _build_app(row):
    app = FastAPI()
    app.include_router(router)

    async def _override_get_db():
        yield _FakeSession(row)

    def _override_user():
        return CurrentUser(id=_USER_ID, country_code="91", mobile="9990001111")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_effective_user] = _override_user
    return app


@pytest.mark.asyncio
async def test_get_run_returns_detail_with_summary():
    run_id = uuid.uuid4()
    app = _build_app(_stub_run(run_id))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/additional-investment/{run_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(run_id)
    assert body["target_bucket"] == "long_term"
    assert body["cadence"] == "lumpsum"
    assert len(body["buys"]) == 1
    assert body["buys"][0]["recommended_fund"] == "ABC Bluechip Fund"
    # Read-time computed headline must surface (spec: "incl summary").
    assert body["summary"] is not None
    assert "title" in body["summary"]


@pytest.mark.asyncio
async def test_get_run_unknown_id_returns_404():
    # Session yields no row -> scalar_one_or_none() is None.
    app = _build_app(None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/additional-investment/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Additional investment run not found"
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/routers/tests/test_router_read_path.py -v
```

Expected: a collection error — `ModuleNotFoundError: No module named 'app.domains.additional_investment.routers'` (the router package/module does not exist yet).

- [ ] **Step 3: Write minimal implementation** — create the router and its package, then add the list-item schema and register the router.

`app/domains/additional_investment/routers/additional_investment_router.py`:

```python
"""FastAPI router — additional-investment run listing and detail.

Read surface over the write-once ``additional_investment_*`` family. Runs are
BUY-only with no status lifecycle, so there is no status-update route (cf. the
rebalancing router). The list endpoint returns light rows; the detail endpoint
eager-loads per-subgroup targets and the fund BUY list so the Invest page gets
one round-trip per run — including the read-time ``summary`` headline computed
by the detail schema.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.additional_investment.models import AdditionalInvestmentRun
from app.domains.additional_investment.schemas import (
    AdditionalInvestmentRunDetailResponse,
    AdditionalInvestmentRunListItem,
)

router = APIRouter(prefix="/additional-investment", tags=["Additional Investment"])


@router.get("/", response_model=list[AdditionalInvestmentRunListItem])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(AdditionalInvestmentRun)
        .where(AdditionalInvestmentRun.user_id == current_user.id)
        .order_by(AdditionalInvestmentRun.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [AdditionalInvestmentRunListItem.model_validate(r) for r in rows]


@router.get("/{run_id}", response_model=AdditionalInvestmentRunDetailResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(AdditionalInvestmentRun)
        .where(
            AdditionalInvestmentRun.id == run_id,
            AdditionalInvestmentRun.user_id == current_user.id,
        )
        .options(
            selectinload(AdditionalInvestmentRun.targets),
            selectinload(AdditionalInvestmentRun.buys),
        )
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Additional investment run not found",
        )
    return AdditionalInvestmentRunDetailResponse.model_validate(run)
```

`app/domains/additional_investment/routers/__init__.py`:

```python
"""FastAPI router package — additional-investment read endpoints."""

from app.domains.additional_investment.routers.additional_investment_router import (
    router,
)

__all__ = ["router"]
```

`app/domains/additional_investment/routers/tests/__init__.py`:

```python
```

Add `AdditionalInvestmentRunListItem` to `app/domains/additional_investment/schemas/__init__.py` (insert after `AdditionalInvestmentRunDetailResponse`; the module already imports `uuid`, `datetime`, `BaseModel`, `ConfigDict`):

```python
class AdditionalInvestmentRunListItem(BaseModel):
    """Light listing row — no eager-loaded children."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    source_allocation_run_id: uuid.UUID
    target_bucket: str
    cadence: str
    deploy_amount_inr: float
    deployed_inr: float
    undeployed_inr: float
    created_at: datetime
    updated_at: datetime
```

…and add `"AdditionalInvestmentRunListItem"` to that module's `__all__` list.

Register the router in `app/routers/__init__.py`. Add the import right after the existing `rebalancing_router` import (lines 20–22):

```python
from app.domains.additional_investment.routers import (
    router as additional_investment_router,
)
```

…and add it to `all_routers` immediately after `rebalancing_router,` (line 47):

```python
    rebalancing_router,
    additional_investment_router,
```

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/routers/tests/test_router_read_path.py -v
```

Expected: `2 passed` — `test_get_run_returns_detail_with_summary` (200, body carries `id`/`target_bucket`/`buys`/non-null `summary`) and `test_get_run_unknown_id_returns_404` (404, `detail="Additional investment run not found"`).

- [ ] **Step 5: Commit**:

```
git add app/domains/additional_investment/routers/ \
        app/domains/additional_investment/schemas/__init__.py \
        app/routers/__init__.py
git commit -m "feat(additional_investment): add read-path router (list + detail) and register it

GET /additional-investment (list) and GET /additional-investment/{run_id}
(detail incl. computed summary; 404 for unknown id). BUY-only/write-once, so
no status route. Adds AdditionalInvestmentRunListItem schema and wires the
router into the app aggregator like rebalancing.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

## Self-Review

- [ ] The orchestrator will run the plan consistency check (cross-task symbol/interface parity, sequential task numbering, and file-path agreement) as a final pass over this document.
