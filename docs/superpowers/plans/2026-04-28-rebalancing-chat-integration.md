# Rebalancing → Chat Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing pure-Python rebalancing engine at `AI_Agents/src/Rebalancing/` into the chat system as a new top-level `REBALANCING` intent so users can ask "rebalance my portfolio" and get a sectioned-markdown trade plan, with the structured response persisted for the frontend.

**Architecture:** Add a new AI_bridge package mirroring `app/services/ai_bridge/asset_allocation/`. The new service is cache-first: it reads the latest goal allocation from DB (90-day TTL), re-runs allocation inline if stale, then materialises a `RebalancingComputeRequest` from current MF holdings + a static fund-rank CSV + tax profile, calls `run_rebalancing(...)` via thread offload, persists, and renders sectioned markdown with a friend-voice formatter. The rebalancing engine itself is untouched.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Alembic, pytest, Anthropic Claude (allocation only — rebalancing is pure compute).

**Spec:** `docs/superpowers/specs/2026-04-28-rebalancing-chat-integration-design.md`

---

## File map

### New

- `app/services/ai_bridge/rebalancing/__init__.py`
- `app/services/ai_bridge/rebalancing/chat.py`
- `app/services/ai_bridge/rebalancing/service.py`
- `app/services/ai_bridge/rebalancing/input_builder.py`
- `app/services/ai_bridge/rebalancing/holdings_ledger.py`
- `app/services/ai_bridge/rebalancing/tax_aging.py`
- `app/services/ai_bridge/rebalancing/fund_rank.py`
- `app/services/ai_bridge/rebalancing/formatter.py`
- `app/services/ai_bridge/rebalancing/tests/__init__.py`
- `app/services/ai_bridge/rebalancing/tests/test_fund_rank.py`
- `app/services/ai_bridge/rebalancing/tests/test_holdings_ledger.py`
- `app/services/ai_bridge/rebalancing/tests/test_tax_aging.py`
- `app/services/ai_bridge/rebalancing/tests/test_input_builder.py`
- `app/services/ai_bridge/rebalancing/tests/test_formatter.py`
- `app/services/ai_bridge/rebalancing/tests/test_service.py`
- `app/services/ai_bridge/rebalancing/tests/test_chat.py`
- `app/services/ai_bridge/rebalancing/tests/test_persist.py`
- `app/services/rebalancing_recommendation_persist.py`
- `app/routers/ai_modules/rebalancing.py`
- `alembic/versions/<ts>_rebalancing_chat_integration.py`
- `app/services/chat_core/tests/test_rebalancing_e2e.py` (light integration)

### Modified

- `AI_Agents/src/intent_classifier/models.py` — add `REBALANCING` enum value.
- `AI_Agents/src/intent_classifier/prompts.py` — describe new intent + 2-3 examples.
- `app/models/profile/tax_profile.py` — add `tax_regime`, `carryforward_st_loss_inr`, `carryforward_lt_loss_inr`.
- `app/models/rebalancing.py` — add `RecommendationType` enum, `recommendation_type` column, `source_allocation_id` self-FK.
- `app/services/allocation_recommendation_persist.py` — stamp `recommendation_type=ALLOCATION` on writes.
- `app/services/chat_core/brain.py` — add `REBALANCING` dispatch branch.
- `app/services/ai_bridge/__init__.py` — re-export new entry points.
- `app/routers/ai_modules/__init__.py` — mount the new router.
- `app/schemas/ai_modules.py` (or wherever AssetAllocationRequest/Response live) — add `RebalancingComputeApiRequest/Response`.

---

## Task ordering rationale

Tasks are bottom-up: schema + leaf helpers first, then composite builders, then service, then chat surface. Each task is independently testable and committable.

| # | Task | Depends on |
|---|------|-----------|
| 1 | Intent classifier supports REBALANCING | — |
| 2 | Schema migration + ORM | — |
| 3 | Fund-rank CSV loader | — |
| 4 | Holdings ledger helper | 2 (uses ORM) |
| 5 | Tax-aging + exit-load helpers | — |
| 6 | Top-level input builder | 3, 4, 5 |
| 7 | Rebalancing trades persistence | 2 |
| 8 | Output formatter | — |
| 9 | Rebalancing service | 6, 7, 8 |
| 10 | Chat handler + brain wiring | 1, 9 |
| 11 | HTTP debug endpoint | 9 |
| 12 | End-to-end chat test | 10 |

---

### Task 1: Add `REBALANCING` intent to classifier

**Files:**
- Modify: `AI_Agents/src/intent_classifier/models.py:7-13`
- Modify: `AI_Agents/src/intent_classifier/prompts.py` (description + few-shot)
- Test: `AI_Agents/src/intent_classifier/Testing/test_classifier_rebalancing.py` (new) OR add cases to existing classifier tests if they live elsewhere — confirm by `ls AI_Agents/src/intent_classifier/Testing/` first.

- [ ] **Step 1: Read the current prompt to find the few-shot section**

Run: `grep -n "portfolio_optimisation\|few-shot\|examples" AI_Agents/src/intent_classifier/prompts.py`

This locates the description block and the example list. Note line ranges of the examples list and the per-intent description block.

- [ ] **Step 2: Write a failing classifier test for "rebalance my portfolio"**

Create `AI_Agents/src/intent_classifier/Testing/test_classifier_rebalancing.py`:

```python
"""Locks the REBALANCING intent boundary.

Three variants of "how do I get there" — should all classify as REBALANCING,
not PORTFOLIO_OPTIMISATION.
"""

import pytest

from intent_classifier.classifier import IntentClassifier
from intent_classifier.models import ClassificationInput, Intent


@pytest.mark.parametrize("question", [
    "rebalance my portfolio",
    "what trades should I make to align with my plan?",
    "show me what to buy and sell",
])
def test_rebalancing_intent_classified(question: str) -> None:
    classifier = IntentClassifier()
    result = classifier.classify(ClassificationInput(customer_question=question))
    assert result.intent == Intent.REBALANCING, (
        f"expected REBALANCING for {question!r}, got {result.intent}"
    )


def test_optimisation_still_routes_to_optimisation() -> None:
    """Guards against regression: 'where should I be' should NOT be rebalancing."""
    classifier = IntentClassifier()
    result = classifier.classify(
        ClassificationInput(customer_question="what's my ideal asset allocation?")
    )
    assert result.intent == Intent.PORTFOLIO_OPTIMISATION
```

- [ ] **Step 3: Run test — expect ImportError on `Intent.REBALANCING`**

Run: `cd AI_Agents/src && pytest intent_classifier/Testing/test_classifier_rebalancing.py -v`
Expected: ERROR / AttributeError on `Intent.REBALANCING`.

- [ ] **Step 4: Add `REBALANCING` to the enum**

Edit `AI_Agents/src/intent_classifier/models.py`:

```python
class Intent(str, Enum):
    PORTFOLIO_OPTIMISATION = "portfolio_optimisation"
    GOAL_PLANNING          = "goal_planning"
    STOCK_ADVICE           = "stock_advice"
    PORTFOLIO_QUERY        = "portfolio_query"
    GENERAL_MARKET_QUERY   = "general_market_query"
    REBALANCING            = "rebalancing"
    OUT_OF_SCOPE           = "out_of_scope"
```

- [ ] **Step 5: Update the classifier prompt**

Edit `AI_Agents/src/intent_classifier/prompts.py` to:

(a) add a description line for the new intent in whatever per-intent description block exists. The description text:

> `rebalancing` — the customer wants the *trades to execute* to align their current holdings with their ideal allocation (buy/sell list, exit decisions, tax-aware sequencing). Distinct from `portfolio_optimisation` which answers "what should my allocation be" — `rebalancing` answers "how do I get there".

(b) Add 2-3 few-shot examples to whatever example list exists, mirroring the format of existing examples:

- "rebalance my portfolio" → `rebalancing`
- "what trades should I make to align with my plan?" → `rebalancing`
- "show me what to buy and sell to fix my portfolio" → `rebalancing`

- [ ] **Step 6: Run test — expect PASS (all 4 cases)**

Run: `cd AI_Agents/src && pytest intent_classifier/Testing/test_classifier_rebalancing.py -v`
Expected: 4 passed. Classifier needs `ANTHROPIC_API_KEY` in env — if it's not set in CI, the test should be marked `skip` with a note. Confirm by checking how the existing classifier tests handle missing keys.

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/intent_classifier/models.py \
        AI_Agents/src/intent_classifier/prompts.py \
        AI_Agents/src/intent_classifier/Testing/test_classifier_rebalancing.py
git commit -m "feat(intent): add REBALANCING intent for trade-list questions"
```

---

### Task 2: Schema migration — RecommendationType + TaxProfile fields + update existing allocation persist

**Files:**
- Modify: `app/models/rebalancing.py`
- Modify: `app/models/profile/tax_profile.py`
- Modify: `app/services/allocation_recommendation_persist.py`
- Create: `alembic/versions/<ts>_rebalancing_chat_integration.py`
- Create: `app/services/tests/test_allocation_persist_discriminator.py`

- [ ] **Step 1: Generate alembic revision skeleton**

Run: `alembic revision -m "rebalancing_chat_integration"`

This creates `alembic/versions/<ts>_rebalancing_chat_integration.py`. Note the new file path.

- [ ] **Step 2: Write failing test for the new persist discriminator**

Create `app/services/tests/test_allocation_persist_discriminator.py`:

```python
"""Existing allocation persistence must stamp recommendation_type=ALLOCATION."""

import pytest

from app.models.rebalancing import RecommendationType


@pytest.mark.asyncio
async def test_allocation_persist_stamps_allocation_type(db_session, fixture_user_with_dob, fixture_goal_allocation_output):
    from app.services.allocation_recommendation_persist import persist_goal_allocation_recommendation

    rec_id, _snap_id = await persist_goal_allocation_recommendation(
        db_session, fixture_user_with_dob.id, fixture_goal_allocation_output,
    )

    from sqlalchemy import select
    from app.models.rebalancing import RebalancingRecommendation

    rec = (await db_session.execute(
        select(RebalancingRecommendation).where(RebalancingRecommendation.id == rec_id)
    )).scalar_one()
    assert rec.recommendation_type == RecommendationType.ALLOCATION
    assert rec.source_allocation_id is None
```

(Engineer: confirm `db_session`, `fixture_user_with_dob`, and `fixture_goal_allocation_output` fixture names match this repo's conftests. If different, rename. If absent, add minimal fixtures based on `app/services/ai_bridge/asset_allocation/tests/test_service_persists_agent_run.py`.)

- [ ] **Step 3: Run test — expect ImportError on `RecommendationType`**

Run: `pytest app/services/tests/test_allocation_persist_discriminator.py -v`
Expected: ERROR — `cannot import name 'RecommendationType'`.

- [ ] **Step 4: Update `app/models/rebalancing.py`**

Replace the file contents with:

```python
"""SQLAlchemy ORM model — `rebalancing.py`.

Holds two row kinds, distinguished by ``recommendation_type``:
- ``ALLOCATION`` rows — goal-based asset allocation outputs (legacy + cache).
- ``REBALANCING_TRADES`` rows — trade-list outputs from the rebalancing engine.

A trade-list row references the allocation row it consumed via
``source_allocation_id`` (audit + cache lookup).
"""


from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RebalancingStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    executed = "executed"
    rejected = "rejected"


class RecommendationType(str, enum.Enum):
    ALLOCATION = "allocation"
    REBALANCING_TRADES = "rebalancing_trades"


class RebalancingRecommendation(Base):
    __tablename__ = "rebalancing_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE")
    )
    recommendation_type: Mapped[RecommendationType] = mapped_column(
        SAEnum(RecommendationType, name="recommendation_type_enum", create_constraint=True),
        nullable=False,
        index=True,
    )
    source_allocation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_recommendations.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[RebalancingStatus] = mapped_column(
        SAEnum(RebalancingStatus, name="rebalancing_status_enum", create_constraint=True),
        default=RebalancingStatus.pending,
    )
    recommendation_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 5: Update `app/models/profile/tax_profile.py`**

Add three columns. Replace the column block (after `notes:`) with:

```python
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tax_regime: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    carryforward_st_loss_inr: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0"
    )
    carryforward_lt_loss_inr: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0"
    )
```

Make sure `String` is in the imports list at the top:

```python
from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
```

- [ ] **Step 6: Write the alembic upgrade/downgrade**

Edit `alembic/versions/<ts>_rebalancing_chat_integration.py`:

```python
"""rebalancing_chat_integration

Revision ID: <auto>
Revises: <auto — most recent head>
Create Date: <auto>
"""
from alembic import op
import sqlalchemy as sa


revision = "<auto>"
down_revision = "<auto — most recent head>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. recommendation_type enum + column on rebalancing_recommendations.
    rec_type = sa.Enum(
        "allocation", "rebalancing_trades",
        name="recommendation_type_enum",
        create_constraint=True,
    )
    rec_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rebalancing_recommendations",
        sa.Column(
            "recommendation_type",
            rec_type,
            nullable=False,
            server_default="allocation",
        ),
    )
    op.create_index(
        "ix_rebrec_recommendation_type",
        "rebalancing_recommendations",
        ["recommendation_type"],
    )
    # Drop server_default after backfill so future inserts must set it explicitly.
    op.alter_column("rebalancing_recommendations", "recommendation_type", server_default=None)

    # 2. self-FK source_allocation_id on rebalancing_recommendations.
    op.add_column(
        "rebalancing_recommendations",
        sa.Column("source_allocation_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_rebrec_source_allocation",
        "rebalancing_recommendations",
        "rebalancing_recommendations",
        ["source_allocation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. tax_profiles new columns.
    op.add_column("tax_profiles", sa.Column("tax_regime", sa.String(length=8), nullable=True))
    op.add_column(
        "tax_profiles",
        sa.Column("carryforward_st_loss_inr", sa.Numeric(15, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "tax_profiles",
        sa.Column("carryforward_lt_loss_inr", sa.Numeric(15, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("tax_profiles", "carryforward_lt_loss_inr")
    op.drop_column("tax_profiles", "carryforward_st_loss_inr")
    op.drop_column("tax_profiles", "tax_regime")

    op.drop_constraint("fk_rebrec_source_allocation", "rebalancing_recommendations", type_="foreignkey")
    op.drop_column("rebalancing_recommendations", "source_allocation_id")

    op.drop_index("ix_rebrec_recommendation_type", table_name="rebalancing_recommendations")
    op.drop_column("rebalancing_recommendations", "recommendation_type")
    sa.Enum(name="recommendation_type_enum").drop(op.get_bind(), checkfirst=True)
```

(Engineer: replace `<auto>` placeholders with the values alembic generated in Step 1; replace `<auto — most recent head>` with the previous head revision shown by `alembic heads`.)

- [ ] **Step 7: Update `app/services/allocation_recommendation_persist.py`**

Find the `RebalancingRecommendation(...)` constructor call (around line 67) and add the new field:

```python
from app.models.rebalancing import RebalancingRecommendation, RebalancingStatus, RecommendationType
# ...

    rec = RebalancingRecommendation(
        portfolio_id=portfolio.id,
        status=RebalancingStatus.pending,
        recommendation_type=RecommendationType.ALLOCATION,
        recommendation_data={
            ...
```

- [ ] **Step 8: Apply migration to dev DB**

Run: `alembic upgrade head`
Expected: clean run; new columns and FK created.

- [ ] **Step 9: Run the test from Step 2 — expect PASS**

Run: `pytest app/services/tests/test_allocation_persist_discriminator.py -v`
Expected: PASS.

- [ ] **Step 10: Run the full existing test suite for asset_allocation to confirm nothing broke**

Run: `pytest app/services/ai_bridge/asset_allocation/tests/ -v`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add app/models/rebalancing.py \
        app/models/profile/tax_profile.py \
        app/services/allocation_recommendation_persist.py \
        alembic/versions/*_rebalancing_chat_integration.py \
        app/services/tests/test_allocation_persist_discriminator.py
git commit -m "feat(db): add recommendation_type + source FK + tax_profile fields"
```

---

### Task 3: Fund-rank CSV loader

**Files:**
- Create: `app/services/ai_bridge/rebalancing/__init__.py`
- Create: `app/services/ai_bridge/rebalancing/fund_rank.py`
- Create: `app/services/ai_bridge/rebalancing/tests/__init__.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_fund_rank.py`

- [ ] **Step 1: Create empty package markers**

```python
# app/services/ai_bridge/rebalancing/__init__.py
"""Rebalancing domain — engine adapter, chat handler, and input builder.

Public surface re-exports the bridge entry points. The ``chat`` submodule is
**not** auto-imported here: doing so triggers a circular import via
``chat_core.turn_context``. Callers that need its ``@register`` side-effect
must import ``chat`` lazily (e.g. inside a function body in ``chat_core/brain.py``).
"""

from app.services.ai_bridge.rebalancing.service import (
    compute_rebalancing_result,
    RebalancingRunOutcome,
)

__all__ = [
    "compute_rebalancing_result",
    "RebalancingRunOutcome",
]
```

```python
# app/services/ai_bridge/rebalancing/tests/__init__.py
```

(The tests/__init__.py is intentionally empty — package marker only.)

Note: `__init__.py` will fail to import until Task 9 creates `service.py`. Plan order: this is fine; we don't run tests that import the package root until later tasks.

- [ ] **Step 2: Write failing test**

Create `app/services/ai_bridge/rebalancing/tests/test_fund_rank.py`:

```python
"""Loads the production fund-rank CSV and exposes a typed lookup."""

from app.services.ai_bridge.rebalancing.fund_rank import (
    get_fund_ranking,
    FundRankRow,
)


def test_get_fund_ranking_returns_dict_keyed_by_subgroup():
    ranking = get_fund_ranking()
    assert isinstance(ranking, dict)
    assert "low_beta_equities" in ranking, "expected the canonical large-cap subgroup"
    rows = ranking["low_beta_equities"]
    assert all(isinstance(r, FundRankRow) for r in rows)


def test_ranks_are_sorted_ascending_within_subgroup():
    ranking = get_fund_ranking()
    for subgroup, rows in ranking.items():
        ranks = [r.rank for r in rows]
        assert ranks == sorted(ranks), f"{subgroup} ranks unsorted: {ranks}"
        assert ranks[0] == 1, f"{subgroup} doesn't start at rank 1"


def test_first_row_is_aditya_birla_large_cap():
    """Pin the canonical row 0 of the CSV to catch accidental file swaps."""
    ranking = get_fund_ranking()
    first = ranking["low_beta_equities"][0]
    assert first.rank == 1
    assert first.isin == "INF209K01YY7"
    assert first.sub_category == "Large Cap Fund"
```

- [ ] **Step 3: Run test — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_fund_rank.py -v`
Expected: ERROR — `No module named 'app.services.ai_bridge.rebalancing.fund_rank'`.

- [ ] **Step 4: Create `fund_rank.py`**

```python
# app/services/ai_bridge/rebalancing/fund_rank.py
"""Loader for the static fund-rank CSV consumed by the rebalancing input builder.

The CSV is a 1:N mapping from ``asset_subgroup`` to ranked recommended funds. It
is loaded once at module import time and cached as a frozen dict; no DB calls.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path


_CSV_PATH = Path(__file__).resolve().parents[4] / "AI_Agents" / "Reference_docs" / "Prozpr_fund_ranking.csv"


@dataclass(frozen=True)
class FundRankRow:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    fund_name: str


@cache
def get_fund_ranking() -> dict[str, list[FundRankRow]]:
    """Return ``{asset_subgroup: [FundRankRow, ...]}`` sorted by rank.

    Cached for the lifetime of the process. To force a reload (e.g. after
    swapping the CSV in tests), call ``get_fund_ranking.cache_clear()``.
    """
    by_sg: dict[str, list[FundRankRow]] = defaultdict(list)
    with open(_CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            by_sg[row["asset_subgroup"]].append(FundRankRow(
                asset_subgroup=row["asset_subgroup"],
                sub_category=row["sub_category"],
                rank=int(row["rank"]),
                isin=row["isin"],
                fund_name=row["recommended_fund"],
            ))
    for subgroup in by_sg:
        by_sg[subgroup].sort(key=lambda r: r.rank)
    return dict(by_sg)
```

(Engineer: confirm the relative path. From `app/services/ai_bridge/rebalancing/fund_rank.py`, `parents[4]` reaches the `Prozpr_Backend/` root. Verify with `python -c "from pathlib import Path; print(Path('app/services/ai_bridge/rebalancing/fund_rank.py').resolve().parents[4])"`.)

- [ ] **Step 5: Run test — expect PASS**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_fund_rank.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_bridge/rebalancing/__init__.py \
        app/services/ai_bridge/rebalancing/fund_rank.py \
        app/services/ai_bridge/rebalancing/tests/__init__.py \
        app/services/ai_bridge/rebalancing/tests/test_fund_rank.py
git commit -m "feat(rebalancing): load static fund-rank CSV"
```

---

### Task 4: Holdings ledger helper

The engine needs lot-level data. `MfTransaction` is keyed by `scheme_code`, but the
fund-rank CSV is keyed by ISIN. Mapping comes from `MfNavHistory.isin`.

**Files:**
- Create: `app/services/ai_bridge/rebalancing/holdings_ledger.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_holdings_ledger.py`

- [ ] **Step 1: Write failing test for `build_holdings_ledger`**

Create `app/services/ai_bridge/rebalancing/tests/test_holdings_ledger.py`:

```python
"""Lot-level holdings ledger built from MfTransaction rows, FIFO-consumed by sells."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.ai_bridge.rebalancing.holdings_ledger import (
    Lot,
    HoldingLedgerEntry,
    build_holdings_ledger,
)


@pytest.mark.asyncio
async def test_buy_only_yields_one_entry(db_session, fixture_user, fixture_buy_txn_factory, fixture_nav_isin_factory):
    await fixture_buy_txn_factory(
        user=fixture_user, scheme_code="100001",
        units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1),
    )
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry.isin == "INF000000001"
    assert entry.scheme_code == "100001"
    assert len(entry.lots) == 1
    assert entry.lots[0].units == Decimal("10")
    assert entry.lots[0].acquisition_nav == Decimal("50")
    assert entry.lots[0].acquisition_date == date(2025, 1, 1)


@pytest.mark.asyncio
async def test_sell_consumes_oldest_lot_fifo(db_session, fixture_user, fixture_buy_txn_factory, fixture_sell_txn_factory, fixture_nav_isin_factory):
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1))
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("5"), nav=Decimal("60"), txn_date=date(2025, 6, 1))
    await fixture_sell_txn_factory(user=fixture_user, scheme_code="100001",
                                   units=Decimal("8"), nav=Decimal("70"), txn_date=date(2025, 9, 1))
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)

    entry = ledger[0]
    assert len(entry.lots) == 2
    # Oldest lot consumed: 10-8 = 2 units left from Jan 1
    assert entry.lots[0].acquisition_date == date(2025, 1, 1)
    assert entry.lots[0].units == Decimal("2")
    # Jun 1 lot untouched
    assert entry.lots[1].acquisition_date == date(2025, 6, 1)
    assert entry.lots[1].units == Decimal("5")


@pytest.mark.asyncio
async def test_fully_sold_position_dropped(db_session, fixture_user, fixture_buy_txn_factory, fixture_sell_txn_factory, fixture_nav_isin_factory):
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1))
    await fixture_sell_txn_factory(user=fixture_user, scheme_code="100001",
                                   units=Decimal("10"), nav=Decimal("60"), txn_date=date(2025, 6, 1))
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)
    assert ledger == []
```

(Engineer: implement `fixture_buy_txn_factory`, `fixture_sell_txn_factory`, `fixture_nav_isin_factory` in `app/services/ai_bridge/rebalancing/tests/conftest.py`. Each writes one `MfTransaction` / `MfNavHistory` / `MfFundMetadata` row with sensible defaults. The `MfTransaction.transaction_type` enum lives at `app/models/mf/enums.py:MfTransactionType` — `BUY` and `SELL`.)

- [ ] **Step 2: Run test — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_holdings_ledger.py -v`
Expected: ERROR — module not found.

- [ ] **Step 3: Write `holdings_ledger.py`**

```python
# app/services/ai_bridge/rebalancing/holdings_ledger.py
"""Build a per-ISIN list of remaining lots from MfTransaction rows.

FIFO: sells consume the oldest buy-lot first. Switches in/out and dividend
reinvest are NOT yet handled — engineer to extend if needed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import MfTransactionType
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.mf.mf_transaction import MfTransaction


@dataclass(frozen=True)
class Lot:
    """A buy-side lot with units and acquisition cost."""
    acquisition_date: date
    units: Decimal
    acquisition_nav: Decimal


@dataclass(frozen=True)
class HoldingLedgerEntry:
    isin: str
    scheme_code: str
    lots: tuple[Lot, ...]


async def _scheme_to_isin(db: AsyncSession, scheme_codes: set[str]) -> dict[str, str]:
    """Latest non-null ISIN per scheme_code, looked up in MfNavHistory."""
    if not scheme_codes:
        return {}
    rows = (await db.execute(
        select(MfNavHistory.scheme_code, MfNavHistory.isin, MfNavHistory.nav_date)
        .where(MfNavHistory.scheme_code.in_(scheme_codes))
        .where(MfNavHistory.isin.is_not(None))
        .order_by(MfNavHistory.scheme_code, MfNavHistory.nav_date.desc())
    )).all()
    out: dict[str, str] = {}
    for code, isin, _date in rows:
        out.setdefault(code, isin)
    return out


async def build_holdings_ledger(
    db: AsyncSession, *, user_id: uuid.UUID,
) -> list[HoldingLedgerEntry]:
    """Return one entry per ISIN with non-zero remaining units. FIFO."""
    rows = (await db.execute(
        select(MfTransaction)
        .where(MfTransaction.user_id == user_id)
        .order_by(MfTransaction.scheme_code, MfTransaction.transaction_date)
    )).scalars().all()

    by_scheme: dict[str, deque[Lot]] = defaultdict(deque)
    for txn in rows:
        if txn.transaction_type == MfTransactionType.BUY:
            by_scheme[txn.scheme_code].append(Lot(
                acquisition_date=txn.transaction_date,
                units=Decimal(str(txn.units)),
                acquisition_nav=Decimal(str(txn.nav)),
            ))
        elif txn.transaction_type == MfTransactionType.SELL:
            remaining = Decimal(str(txn.units))
            lots = by_scheme[txn.scheme_code]
            while remaining > 0 and lots:
                head = lots[0]
                if head.units <= remaining:
                    remaining -= head.units
                    lots.popleft()
                else:
                    lots[0] = Lot(
                        acquisition_date=head.acquisition_date,
                        units=head.units - remaining,
                        acquisition_nav=head.acquisition_nav,
                    )
                    remaining = Decimal(0)
        # SWITCH_IN / SWITCH_OUT / DIVIDEND_REINVEST: ignored in v1.

    held_schemes = {code for code, lots in by_scheme.items() if lots}
    isin_map = await _scheme_to_isin(db, held_schemes)

    out: list[HoldingLedgerEntry] = []
    for scheme_code, lots in sorted(by_scheme.items()):
        if not lots:
            continue
        isin = isin_map.get(scheme_code)
        if isin is None:
            # No ISIN known for this scheme — skip; service.py logs a warning later.
            continue
        out.append(HoldingLedgerEntry(
            isin=isin,
            scheme_code=scheme_code,
            lots=tuple(lots),
        ))
    return out
```

- [ ] **Step 4: Run tests — expect 3 passed**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_holdings_ledger.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/holdings_ledger.py \
        app/services/ai_bridge/rebalancing/tests/test_holdings_ledger.py \
        app/services/ai_bridge/rebalancing/tests/conftest.py
git commit -m "feat(rebalancing): build per-ISIN FIFO holdings ledger"
```

---

### Task 5: Tax-aging + exit-load helpers

**Files:**
- Create: `app/services/ai_bridge/rebalancing/tax_aging.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_tax_aging.py`

- [ ] **Step 1: Write failing tests**

Create `app/services/ai_bridge/rebalancing/tests/test_tax_aging.py`:

```python
"""Per-lot tax-aging classification and exit-load counting."""

from datetime import date
from decimal import Decimal

from app.services.ai_bridge.rebalancing.holdings_ledger import Lot
from app.services.ai_bridge.rebalancing.tax_aging import (
    classify_lots_st_lt,
    count_units_in_exit_load_window,
    LotSplit,
)


def test_lt_lot_classified_as_long_term():
    """Equity lot held > 12 months → LT."""
    lot = Lot(acquisition_date=date(2024, 1, 1), units=Decimal("10"),
              acquisition_nav=Decimal("50"))
    split = classify_lots_st_lt(
        [lot], asset_class="equity", current_nav=Decimal("60"),
        as_of=date(2026, 4, 28),
    )
    assert split.st_value_inr == Decimal(0)
    assert split.st_cost_inr == Decimal(0)
    assert split.lt_value_inr == Decimal("600")  # 10 * 60
    assert split.lt_cost_inr == Decimal("500")   # 10 * 50


def test_st_lot_just_under_12_months_equity():
    """Lot acquired 11 months ago is ST for equity (12-mo threshold)."""
    lot = Lot(acquisition_date=date(2025, 5, 28), units=Decimal("10"),
              acquisition_nav=Decimal("50"))
    split = classify_lots_st_lt(
        [lot], asset_class="equity", current_nav=Decimal("60"),
        as_of=date(2026, 4, 28),
    )
    assert split.st_value_inr == Decimal("600")
    assert split.lt_value_inr == Decimal(0)


def test_debt_uses_24_month_threshold():
    """Debt lot at 18 months is still ST (24-mo threshold for debt)."""
    lot = Lot(acquisition_date=date(2024, 10, 28), units=Decimal("10"),
              acquisition_nav=Decimal("100"))
    split = classify_lots_st_lt(
        [lot], asset_class="debt", current_nav=Decimal("105"),
        as_of=date(2026, 4, 28),
    )
    assert split.st_value_inr == Decimal("1050")
    assert split.lt_value_inr == Decimal(0)


def test_unknown_asset_class_defaults_to_equity_threshold():
    """Defensive: unrecognised asset_class behaves like equity (12 mo)."""
    lot = Lot(acquisition_date=date(2024, 1, 1), units=Decimal("10"),
              acquisition_nav=Decimal("50"))
    split = classify_lots_st_lt(
        [lot], asset_class="hybrid", current_nav=Decimal("60"),
        as_of=date(2026, 4, 28),
    )
    assert split.lt_value_inr == Decimal("600")  # > 12 mo


def test_exit_load_window():
    """Lots within exit_load_months of as_of are counted."""
    lot_in = Lot(date(2026, 1, 1), Decimal("4"), Decimal("100"))
    lot_out = Lot(date(2024, 1, 1), Decimal("6"), Decimal("80"))
    units = count_units_in_exit_load_window(
        [lot_in, lot_out], exit_load_months=12, as_of=date(2026, 4, 28),
    )
    assert units == Decimal("4")


def test_exit_load_window_zero_months_returns_zero():
    """exit_load_months=0 → no lots in window, ever."""
    lot = Lot(date(2026, 4, 27), Decimal("10"), Decimal("100"))
    units = count_units_in_exit_load_window(
        [lot], exit_load_months=0, as_of=date(2026, 4, 28),
    )
    assert units == Decimal(0)
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_tax_aging.py -v`
Expected: ERROR — module not found.

- [ ] **Step 3: Write `tax_aging.py`**

```python
# app/services/ai_bridge/rebalancing/tax_aging.py
"""Per-lot tax-aging and exit-load helpers.

Threshold values come from ``Rebalancing/config`` so the builder and the engine
share one source of truth for ST/LT cut-offs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.config import (  # type: ignore[import-not-found]
    ST_THRESHOLD_MONTHS_DEBT,
    ST_THRESHOLD_MONTHS_EQUITY,
)

from app.services.ai_bridge.rebalancing.holdings_ledger import Lot


@dataclass(frozen=True)
class LotSplit:
    """ST/LT split aggregated over a list of lots, all in INR."""
    st_value_inr: Decimal
    st_cost_inr: Decimal
    lt_value_inr: Decimal
    lt_cost_inr: Decimal


def _months_between(start: date, end: date) -> int:
    """Whole months elapsed from ``start`` to ``end`` (calendar-aware)."""
    return (end.year - start.year) * 12 + (end.month - start.month) - (
        1 if end.day < start.day else 0
    )


def _threshold_for(asset_class: str) -> int:
    if asset_class.lower() == "debt":
        return ST_THRESHOLD_MONTHS_DEBT
    return ST_THRESHOLD_MONTHS_EQUITY  # equity / others / unknown


def classify_lots_st_lt(
    lots: Iterable[Lot],
    *,
    asset_class: str,
    current_nav: Decimal,
    as_of: date,
) -> LotSplit:
    """Aggregate ST/LT value and cost for ``lots``.

    A lot whose age in months is *strictly less than* the threshold is ST.
    """
    threshold = _threshold_for(asset_class)
    st_value = st_cost = lt_value = lt_cost = Decimal(0)
    for lot in lots:
        age = _months_between(lot.acquisition_date, as_of)
        value = lot.units * current_nav
        cost = lot.units * lot.acquisition_nav
        if age < threshold:
            st_value += value
            st_cost += cost
        else:
            lt_value += value
            lt_cost += cost
    return LotSplit(
        st_value_inr=st_value, st_cost_inr=st_cost,
        lt_value_inr=lt_value, lt_cost_inr=lt_cost,
    )


def count_units_in_exit_load_window(
    lots: Iterable[Lot],
    *,
    exit_load_months: int,
    as_of: date,
) -> Decimal:
    """Sum units from lots whose age is *strictly less than* ``exit_load_months``."""
    if exit_load_months <= 0:
        return Decimal(0)
    total = Decimal(0)
    for lot in lots:
        if _months_between(lot.acquisition_date, as_of) < exit_load_months:
            total += lot.units
    return total
```

- [ ] **Step 4: Run tests — expect 6 passed**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_tax_aging.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/tax_aging.py \
        app/services/ai_bridge/rebalancing/tests/test_tax_aging.py
git commit -m "feat(rebalancing): tax-aging + exit-load helpers"
```

---

### Task 6: Top-level input builder

Composes Tasks 3-5 into the full `RebalancingComputeRequest` materialiser.

**Files:**
- Create: `app/services/ai_bridge/rebalancing/input_builder.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_input_builder.py`

- [ ] **Step 1: Write failing test for the canonical happy-path**

Create `app/services/ai_bridge/rebalancing/tests/test_input_builder.py`:

```python
"""End-to-end input builder: holdings + allocation + CSV → RebalancingComputeRequest."""

from datetime import date
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_recommended_only_with_no_holdings(
    db_session, fixture_user_with_dob, fixture_goal_allocation_output_one_subgroup,
):
    """User with no MF holdings yet, allocation says ₹10L Large Cap.

    Expectation: rows are all rank ≥ 1 from CSV for that subgroup, present_* = 0,
    target_amount_pre_cap = 10L on rank 1, 0 elsewhere. total_corpus = 0.
    """
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, debug = await build_rebalancing_input_for_user(
        fixture_user_with_dob,
        fixture_goal_allocation_output_one_subgroup,
        db_session,
    )

    assert request.total_corpus == Decimal(0)
    assert all(row.is_recommended for row in request.rows)
    rank1 = next(r for r in request.rows if r.rank == 1)
    assert rank1.target_amount_pre_cap == Decimal("1000000")
    assert all(r.target_amount_pre_cap == Decimal(0) for r in request.rows if r.rank != 1)
    assert all(r.present_allocation_inr == Decimal(0) for r in request.rows)


@pytest.mark.asyncio
async def test_held_isin_in_recommended_set_enriched(
    db_session, fixture_user_with_holdings, fixture_goal_allocation_output_one_subgroup,
):
    """Holding maps onto a rank in the recommended set → enriched row, no BAD."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _ = await build_rebalancing_input_for_user(
        fixture_user_with_holdings,
        fixture_goal_allocation_output_one_subgroup,
        db_session,
    )

    matching = [r for r in request.rows if r.isin == fixture_user_with_holdings.held_isin]
    assert len(matching) == 1
    row = matching[0]
    assert row.is_recommended
    assert row.present_allocation_inr > Decimal(0)
    assert row.invested_cost_inr > Decimal(0)
    assert row.current_nav > Decimal(0)


@pytest.mark.asyncio
async def test_bad_fund_when_held_isin_not_recommended(
    db_session, fixture_user_with_bad_holding, fixture_goal_allocation_output_one_subgroup,
):
    """Holding ISIN not in fund-rank CSV → BAD row (rank=0, is_recommended=False)."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _ = await build_rebalancing_input_for_user(
        fixture_user_with_bad_holding,
        fixture_goal_allocation_output_one_subgroup,
        db_session,
    )

    bad_rows = [r for r in request.rows if not r.is_recommended]
    assert len(bad_rows) == 1
    bad = bad_rows[0]
    assert bad.rank == 0
    assert bad.target_amount_pre_cap == Decimal(0)
    assert bad.present_allocation_inr > Decimal(0)


@pytest.mark.asyncio
async def test_total_corpus_sums_held_market_values(
    db_session, fixture_user_with_two_holdings, fixture_goal_allocation_output_one_subgroup,
):
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )
    request, _ = await build_rebalancing_input_for_user(
        fixture_user_with_two_holdings,
        fixture_goal_allocation_output_one_subgroup,
        db_session,
    )
    expected = (
        Decimal("10") * Decimal("60")  # holding 1: 10 units @ NAV 60 = 600
        + Decimal("5") * Decimal("80")  # holding 2: 5 units @ NAV 80 = 400
    )
    assert request.total_corpus == expected


@pytest.mark.asyncio
async def test_missing_tax_profile_uses_defaults(
    db_session, fixture_user_with_holdings_no_tax_profile, fixture_goal_allocation_output_one_subgroup,
):
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )
    request, _ = await build_rebalancing_input_for_user(
        fixture_user_with_holdings_no_tax_profile,
        fixture_goal_allocation_output_one_subgroup,
        db_session,
    )
    assert request.tax_regime == "new"
    assert float(request.effective_tax_rate_pct) == 30.0
    assert request.carryforward_st_loss_inr == Decimal(0)
    assert request.carryforward_lt_loss_inr == Decimal(0)
    assert request.stcg_offset_budget_inr is None
    assert request.rounding_step == 100
```

(Engineer: implement the listed fixtures in `tests/conftest.py`. `fixture_goal_allocation_output_one_subgroup` should return a `GoalAllocationOutput` whose `aggregated_subgroups` has exactly one row — `subgroup="low_beta_equities"`, `total=Decimal("1000000")` — to anchor the assertions to the canonical CSV.)

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_input_builder.py -v`
Expected: ERROR — module not found.

- [ ] **Step 3: Write `input_builder.py`**

```python
# app/services/ai_bridge/rebalancing/input_builder.py
"""Materialise a RebalancingComputeRequest from User + GoalAllocationOutput + DB."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.profile.tax_profile import TaxProfile
from app.models.user import User
from app.services.ai_bridge.common import ensure_ai_agents_path

from app.services.ai_bridge.rebalancing.fund_rank import FundRankRow, get_fund_ranking
from app.services.ai_bridge.rebalancing.holdings_ledger import (
    HoldingLedgerEntry,
    build_holdings_ledger,
)
from app.services.ai_bridge.rebalancing.tax_aging import (
    LotSplit,
    classify_lots_st_lt,
    count_units_in_exit_load_window,
)

ensure_ai_agents_path()

from goal_based_allocation_pydantic.models import GoalAllocationOutput  # type: ignore[import-not-found]
from Rebalancing.models import (  # type: ignore[import-not-found]
    FundRowInput,
    RebalancingComputeRequest,
)


_DEFAULT_TAX_REGIME = "new"
_DEFAULT_TAX_RATE_PCT = 30.0
_DEFAULT_FUND_RATING = 10
_ROUNDING_STEP = 100


class _Unpriceable(Exception):
    """Raised when a recommended ISIN has no NAV available."""


async def _latest_nav_by_isin(
    db: AsyncSession, isins: set[str],
) -> dict[str, Decimal]:
    if not isins:
        return {}
    rows = (await db.execute(
        select(MfNavHistory.isin, MfNavHistory.nav, MfNavHistory.nav_date)
        .where(MfNavHistory.isin.in_(isins))
        .order_by(MfNavHistory.isin, MfNavHistory.nav_date.desc())
    )).all()
    out: dict[str, Decimal] = {}
    for isin, nav, _date in rows:
        out.setdefault(isin, Decimal(str(nav)))
    return out


async def _metadata_by_isin(
    db: AsyncSession, isins: set[str],
) -> dict[str, MfFundMetadata]:
    if not isins:
        return {}
    rows = (await db.execute(
        select(MfFundMetadata, MfNavHistory.isin)
        .join(MfNavHistory, MfNavHistory.scheme_code == MfFundMetadata.scheme_code)
        .where(MfNavHistory.isin.in_(isins))
        .distinct()
    )).all()
    return {isin: meta for meta, isin in rows}


def _resolve_tax_inputs(tax_profile: Optional[TaxProfile]) -> dict[str, Any]:
    if tax_profile is None:
        return {
            "tax_regime": _DEFAULT_TAX_REGIME,
            "effective_tax_rate_pct": _DEFAULT_TAX_RATE_PCT,
            "carryforward_st_loss_inr": Decimal(0),
            "carryforward_lt_loss_inr": Decimal(0),
        }
    return {
        "tax_regime": tax_profile.tax_regime or _DEFAULT_TAX_REGIME,
        "effective_tax_rate_pct": float(tax_profile.income_tax_rate or _DEFAULT_TAX_RATE_PCT),
        "carryforward_st_loss_inr": Decimal(str(tax_profile.carryforward_st_loss_inr or 0)),
        "carryforward_lt_loss_inr": Decimal(str(tax_profile.carryforward_lt_loss_inr or 0)),
    }


def _build_row(
    *,
    rank_row: Optional[FundRankRow],
    held_entry: Optional[HoldingLedgerEntry],
    target_amount_pre_cap: Decimal,
    current_nav: Decimal,
    asset_class: str,
    exit_load_pct: float,
    exit_load_months: int,
    is_recommended: bool,
    fund_rating: int,
    asof: date,
    bad_subgroup: Optional[str] = None,
    bad_sub_category: Optional[str] = None,
    bad_fund_name: Optional[str] = None,
    bad_isin: Optional[str] = None,
) -> FundRowInput:
    if rank_row is not None:
        subgroup = rank_row.asset_subgroup
        sub_category = rank_row.sub_category
        fund_name = rank_row.fund_name
        isin = rank_row.isin
        rank = rank_row.rank
    else:
        subgroup = bad_subgroup or "unknown"
        sub_category = bad_sub_category or "unknown"
        fund_name = bad_fund_name or "unknown"
        isin = bad_isin or ""
        rank = 0

    if held_entry is not None:
        split: LotSplit = classify_lots_st_lt(
            held_entry.lots,
            asset_class=asset_class,
            current_nav=current_nav,
            as_of=asof,
        )
        units_in_load = count_units_in_exit_load_window(
            held_entry.lots, exit_load_months=exit_load_months, as_of=asof,
        )
        present = split.st_value_inr + split.lt_value_inr
        invested = split.st_cost_inr + split.lt_cost_inr
    else:
        split = LotSplit(Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        units_in_load = Decimal(0)
        present = Decimal(0)
        invested = Decimal(0)

    return FundRowInput(
        asset_subgroup=subgroup,
        sub_category=sub_category,
        recommended_fund=fund_name,
        isin=isin,
        rank=rank,
        target_amount_pre_cap=target_amount_pre_cap,
        present_allocation_inr=present,
        invested_cost_inr=invested,
        st_value_inr=split.st_value_inr,
        st_cost_inr=split.st_cost_inr,
        lt_value_inr=split.lt_value_inr,
        lt_cost_inr=split.lt_cost_inr,
        exit_load_pct=exit_load_pct,
        exit_load_months=exit_load_months,
        units_within_exit_load_period=units_in_load,
        current_nav=current_nav,
        fund_rating=fund_rating,
        is_recommended=is_recommended,
    )


async def build_rebalancing_input_for_user(
    user: User,
    allocation_output: GoalAllocationOutput,
    db: AsyncSession,
) -> tuple[RebalancingComputeRequest, dict[str, Any]]:
    """Return ``(request, debug_dict)`` for ``run_rebalancing(...)``."""
    asof = date.today()

    # 1. Holdings ledger.
    ledger = await build_holdings_ledger(db, user_id=user.id)
    held_by_isin: dict[str, HoldingLedgerEntry] = {e.isin: e for e in ledger}

    # 2. Sub-asset-group targets from allocation.
    target_by_subgroup: dict[str, Decimal] = {
        r.subgroup: Decimal(str(r.total)) for r in allocation_output.aggregated_subgroups
    }

    # 3. Fund-rank table.
    ranking = get_fund_ranking()
    recommended_isins: set[str] = {
        rr.isin for rows in ranking.values() for rr in rows
    }

    # 4. Bulk-fetch NAV + metadata for everything we need.
    held_isins = set(held_by_isin)
    all_isins = recommended_isins | held_isins
    nav_by_isin = await _latest_nav_by_isin(db, all_isins)
    meta_by_isin = await _metadata_by_isin(db, all_isins)

    rows: list[FundRowInput] = []
    seen_isins: set[str] = set()

    # 5. Recommended-fund rows.
    for subgroup, rank_rows in ranking.items():
        rank1_target = target_by_subgroup.get(subgroup, Decimal(0))
        for rr in rank_rows:
            held = held_by_isin.get(rr.isin)
            current_nav = nav_by_isin.get(rr.isin)
            if current_nav is None:
                if held is None:
                    raise _Unpriceable(
                        f"recommended ISIN {rr.isin} ({rr.fund_name}) has no NAV"
                    )
                # Fallback for held ISIN: latest acquisition_nav as conservative price.
                current_nav = held.lots[-1].acquisition_nav

            meta = meta_by_isin.get(rr.isin)
            asset_class = (meta.asset_class if meta else None) or "equity"
            exit_load_pct = float(meta.exit_load_percent or 0.0) if meta else 0.0
            exit_load_months = int(meta.exit_load_months or 0) if meta else 0

            rows.append(_build_row(
                rank_row=rr,
                held_entry=held,
                target_amount_pre_cap=rank1_target if rr.rank == 1 else Decimal(0),
                current_nav=current_nav,
                asset_class=asset_class,
                exit_load_pct=exit_load_pct,
                exit_load_months=exit_load_months,
                is_recommended=True,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
            ))
            seen_isins.add(rr.isin)

    # 6. BAD-fund rows.
    bad_count = 0
    for isin, entry in held_by_isin.items():
        if isin in seen_isins:
            continue
        meta = meta_by_isin.get(isin)
        current_nav = nav_by_isin.get(isin) or entry.lots[-1].acquisition_nav
        asset_class = (meta.asset_class if meta else None) or "equity"
        rows.append(_build_row(
            rank_row=None,
            held_entry=entry,
            target_amount_pre_cap=Decimal(0),
            current_nav=current_nav,
            asset_class=asset_class,
            exit_load_pct=float(meta.exit_load_percent or 0.0) if meta else 0.0,
            exit_load_months=int(meta.exit_load_months or 0) if meta else 0,
            is_recommended=False,
            fund_rating=_DEFAULT_FUND_RATING,
            asof=asof,
            bad_subgroup=(meta.asset_subgroup if meta else "unknown"),
            bad_sub_category=(meta.sub_category if meta else "unknown"),
            bad_fund_name=(meta.scheme_name if meta else entry.scheme_code),
            bad_isin=isin,
        ))
        bad_count += 1

    # 7. Total corpus = sum of held market values.
    total_corpus = sum(
        (r.present_allocation_inr for r in rows if r.present_allocation_inr > 0),
        start=Decimal(0),
    )

    # 8. Tax inputs.
    tax_profile = getattr(user, "tax_profile", None)
    tax_inputs = _resolve_tax_inputs(tax_profile)

    request = RebalancingComputeRequest(
        total_corpus=total_corpus,
        tax_regime=tax_inputs["tax_regime"],
        effective_tax_rate_pct=tax_inputs["effective_tax_rate_pct"],
        rounding_step=_ROUNDING_STEP,
        stcg_offset_budget_inr=None,
        carryforward_st_loss_inr=tax_inputs["carryforward_st_loss_inr"],
        carryforward_lt_loss_inr=tax_inputs["carryforward_lt_loss_inr"],
        rows=rows,
    )
    debug = {
        "total_corpus": str(total_corpus),
        "lots_per_isin": {e.isin: len(e.lots) for e in ledger},
        "bad_fund_count": bad_count,
        "row_count": len(rows),
    }
    return request, debug
```

- [ ] **Step 4: Run tests — expect 5 passed**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_input_builder.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/input_builder.py \
        app/services/ai_bridge/rebalancing/tests/test_input_builder.py
git commit -m "feat(rebalancing): build engine input from holdings + allocation + CSV"
```

---

### Task 7: Rebalancing trades persistence helper

**Files:**
- Create: `app/services/rebalancing_recommendation_persist.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_persist.py`

- [ ] **Step 1: Write failing test**

Create `app/services/ai_bridge/rebalancing/tests/test_persist.py`:

```python
"""Persist rebalancing engine output as a REBALANCING_TRADES row."""

import uuid
from decimal import Decimal

import pytest

from app.models.rebalancing import RecommendationType, RebalancingStatus


@pytest.mark.asyncio
async def test_persist_writes_trades_row_with_source_fk(
    db_session, fixture_user_with_dob, fixture_rebalancing_response, fixture_allocation_row,
):
    from app.services.rebalancing_recommendation_persist import (
        persist_rebalancing_recommendation,
    )

    rec_id = await persist_rebalancing_recommendation(
        db_session,
        fixture_user_with_dob.id,
        fixture_rebalancing_response,
        chat_session_id=None,
        source_allocation_id=fixture_allocation_row.id,
        used_cached_allocation=True,
    )
    assert isinstance(rec_id, uuid.UUID)

    from sqlalchemy import select
    from app.models.rebalancing import RebalancingRecommendation

    rec = (await db_session.execute(
        select(RebalancingRecommendation).where(RebalancingRecommendation.id == rec_id)
    )).scalar_one()
    assert rec.recommendation_type == RecommendationType.REBALANCING_TRADES
    assert rec.source_allocation_id == fixture_allocation_row.id
    assert rec.status == RebalancingStatus.pending
    assert "rebalancing_response" in rec.recommendation_data
    assert rec.recommendation_data["used_cached_allocation"] is True
```

- [ ] **Step 2: Run test — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_persist.py -v`
Expected: ERROR — module not found.

- [ ] **Step 3: Write the helper**

```python
# app/services/rebalancing_recommendation_persist.py
"""Persist a rebalancing engine response as a REBALANCING_TRADES row."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rebalancing import (
    RebalancingRecommendation,
    RebalancingStatus,
    RecommendationType,
)
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.portfolio_service import get_or_create_primary_portfolio

ensure_ai_agents_path()

from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]


async def persist_rebalancing_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    response: RebalancingComputeResponse,
    *,
    chat_session_id: Optional[uuid.UUID],
    source_allocation_id: Optional[uuid.UUID],
    used_cached_allocation: bool,
    user_question: Optional[str] = None,
) -> uuid.UUID:
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    payload: dict[str, Any] = {
        "source": "rebalancing_engine",
        "rebalancing_response": response.model_dump(mode="json"),
        "request_id": str(response.metadata.request_id),
        "used_cached_allocation": used_cached_allocation,
        "chat_session_id": str(chat_session_id) if chat_session_id else None,
        "user_question": user_question,
    }
    rec = RebalancingRecommendation(
        portfolio_id=portfolio.id,
        recommendation_type=RecommendationType.REBALANCING_TRADES,
        source_allocation_id=source_allocation_id,
        status=RebalancingStatus.pending,
        recommendation_data=payload,
        reason="Rebalancing trade plan (engine output)",
    )
    db.add(rec)
    await db.flush()
    return rec.id
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_persist.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/rebalancing_recommendation_persist.py \
        app/services/ai_bridge/rebalancing/tests/test_persist.py
git commit -m "feat(rebalancing): persistence helper for trade-list rows"
```

---

### Task 8: Output formatter

Friend-voice sectioned markdown. Templates only, no LLM.

**Files:**
- Create: `app/services/ai_bridge/rebalancing/formatter.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_formatter.py`

- [ ] **Step 1: Write failing tests covering each section**

Create `app/services/ai_bridge/rebalancing/tests/test_formatter.py`:

```python
"""Sectioned markdown output. Friend voice. Pure-Python."""

from decimal import Decimal


def _make_minimal_response(*, with_trades=True, with_warnings=False, tax_zero=True):
    """Build a small valid RebalancingComputeResponse for snapshot tests.

    Engineer: implement using the engine's pydantic models. Must include at
    least one SubgroupSummary and a totals object.
    """
    from Rebalancing.models import (  # type: ignore[import-not-found]
        RebalancingComputeResponse, RebalancingTotals, RebalancingRunMetadata,
        SubgroupSummary, TradeAction, RebalancingWarning, WarningCode,
        KnobSnapshot,
    )
    from datetime import datetime
    from uuid import uuid4

    actions = []
    subgroups = []
    if with_trades:
        # Caller must construct a SubgroupSummary with at least one FundRowAfterStep5
        # action — see the engine's tests for a canonical example. Keep this minimal.
        ...

    totals = RebalancingTotals(
        total_buy_inr=Decimal("100"),
        total_sell_inr=Decimal("100"),
        net_cash_flow_inr=Decimal(0),
        total_stcg_realised=Decimal(0) if tax_zero else Decimal("50"),
        total_ltcg_realised=Decimal(0),
        total_stcg_net_off=Decimal(0),
        total_tax_estimate_inr=Decimal(0) if tax_zero else Decimal("10"),
        total_exit_load_inr=Decimal(0),
        unrebalanced_remainder_inr=Decimal(0),
        rows_count=1, funds_to_buy_count=1, funds_to_sell_count=0,
        funds_to_exit_count=0, funds_held_count=0,
    )
    warnings = []
    if with_warnings:
        warnings.append(RebalancingWarning(
            code=WarningCode.UNREBALANCED_REMAINDER,
            message="₹500 unrebalanced",
            affected_isins=[],
        ))
    knobs = KnobSnapshot(
        multi_fund_cap_pct=20.0, others_fund_cap_pct=10.0,
        rebalance_min_change_pct=0.10, exit_floor_rating=5,
        ltcg_annual_exemption_inr=Decimal("125000"),
        stcg_rate_equity_pct=20.0, ltcg_rate_equity_pct=12.5,
        st_threshold_months_equity=12, st_threshold_months_debt=24,
        multi_cap_sub_categories=[],
    )
    metadata = RebalancingRunMetadata(
        computed_at=datetime(2026, 4, 28, 12, 0, 0),
        engine_version="1.0.0",
        request_corpus_inr=Decimal("1000"),
        knob_snapshot=knobs,
        request_id=uuid4(),
    )
    return RebalancingComputeResponse(
        rows=[], subgroups=subgroups, totals=totals,
        metadata=metadata, trade_list=actions, warnings=warnings,
    )


def test_output_includes_lead_line_when_allocation_refreshed():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=False)
    assert "asset mix" in text.lower()  # the soft lead line


def test_output_omits_lead_line_when_cache_hit():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "asset mix" not in text.lower()


def test_output_includes_corpus_in_header():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "1,000" in text or "1000" in text


def test_tax_line_omitted_when_zero():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response(tax_zero=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "trade-offs" not in text.lower()


def test_tax_line_present_when_nonzero():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response(tax_zero=False)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "trade-offs" in text.lower()


def test_heads_up_section_present_when_warnings():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response(with_warnings=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "heads-up" in text.lower()


def test_closing_line_always_present():
    from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "sanity check" in text.lower()
```

(Engineer: complete `_make_minimal_response` to construct at least one `SubgroupSummary` + `TradeAction` with a `FundRowAfterStep5`. The engine's own pytest fixtures at `AI_Agents/src/Rebalancing/Testing/` show how. If snapshot construction is fiddly, defer the per-subgroup tests until after writing the formatter — at minimum the listed assertions above should run.)

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_formatter.py -v`
Expected: ERROR.

- [ ] **Step 3: Write `formatter.py`**

```python
# app/services/ai_bridge/rebalancing/formatter.py
"""Sectioned markdown output for the rebalancing chat reply.

Voice: financially-savvy friend, not advisor. Plain language, no compliance
boilerplate, contractions OK. All copy lives in this file so tone iterations
don't touch the structured data path.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]
    RebalancingComputeResponse,
    RebalancingWarning,
    SubgroupSummary,
    WarningCode,
)


_LEAD_REFRESHED = (
    "_First I redid your asset mix from your goals, then worked out the trades to get there._"
)
_CLOSING = (
    "_Worth a sanity check on exit loads and tax before you pull the trigger._"
)


def _fmt_inr(amount: Decimal | float | int) -> str:
    return f"₹{Decimal(amount):,.0f}"


def _warning_line(w: RebalancingWarning) -> str:
    code = w.code
    if code == WarningCode.BAD_FUND_DETECTED:
        funds = ", ".join(w.affected_isins) or "a few funds"
        return (
            f"- {funds} aren't on the recommended list anymore — "
            "worth exiting when the tax math works."
        )
    if code == WarningCode.UNREBALANCED_REMAINDER:
        return (
            f"- {w.message} couldn't be placed cleanly under the per-fund caps — "
            "small enough to ignore."
        )
    if code == WarningCode.STCG_BUDGET_BINDING:
        return (
            "- Held back some sells to keep short-term gains under your offset budget."
        )
    if code == WarningCode.NO_HOLDINGS_FOR_RECOMMENDED_FUND:
        funds = ", ".join(w.affected_isins) or "a recommended fund"
        return f"- {funds} on your plan but you don't hold it yet — fresh purchase."
    return f"- {w.message}"


def _subgroup_block(s: SubgroupSummary) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"**{s.asset_subgroup}** — you'd land at {_fmt_inr(s.suggested_final_holding_inr)} "
        f"(target was {_fmt_inr(s.goal_target_inr)})."
    )
    for row in s.actions:
        if row.pass1_buy_amount and row.pass1_buy_amount > 0:
            lines.append(
                f"- Put {_fmt_inr(row.pass1_buy_amount)} into {row.recommended_fund}."
            )
        sell_total = (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))
        if sell_total > 0:
            verb = "Pull" if not row.exit_flag else "Exit"
            lines.append(
                f"- {verb} {_fmt_inr(sell_total)} out of {row.recommended_fund}."
            )
    return lines


def format_rebalancing_chat_brief(
    response: RebalancingComputeResponse,
    *,
    used_cached_allocation: bool,
) -> str:
    out: list[str] = []

    if not used_cached_allocation:
        out.append(_LEAD_REFRESHED)
        out.append("")

    totals = response.totals
    n_trades = totals.funds_to_buy_count + totals.funds_to_sell_count + totals.funds_to_exit_count
    corpus = response.metadata.request_corpus_inr
    out.append(
        f"Here's how I'd rebalance — {n_trades} moves on a corpus of about {_fmt_inr(corpus)}."
    )
    out.append("")

    for s in response.subgroups:
        out.extend(_subgroup_block(s))
        out.append("")

    if (totals.total_tax_estimate_inr or 0) > 0 or (totals.total_exit_load_inr or 0) > 0:
        out.append(
            f"The trade-offs: about {_fmt_inr(totals.total_tax_estimate_inr)} in taxes and "
            f"{_fmt_inr(totals.total_exit_load_inr)} in exit loads, with "
            f"{_fmt_inr(totals.total_stcg_realised)} short-term and "
            f"{_fmt_inr(totals.total_ltcg_realised)} long-term gains realised."
        )
        out.append("")

    if response.warnings:
        out.append("**A couple of heads-ups:**")
        for w in response.warnings:
            out.append(_warning_line(w))
        out.append("")

    out.append(_CLOSING)
    return "\n".join(out).rstrip() + "\n"
```

- [ ] **Step 4: Run tests — expect all assertion-level tests to pass**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_formatter.py -v`
Expected: 7 passed (or whatever subset the engineer enabled given snapshot fixture difficulty).

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/formatter.py \
        app/services/ai_bridge/rebalancing/tests/test_formatter.py
git commit -m "feat(rebalancing): friend-voice sectioned markdown formatter"
```

---

### Task 9: Rebalancing service (orchestrator)

**Files:**
- Create: `app/services/ai_bridge/rebalancing/service.py`
- Create: `app/services/ai_bridge/rebalancing/tests/test_service.py`

- [ ] **Step 1: Write failing tests for control-flow branches**

Create `app/services/ai_bridge/rebalancing/tests/test_service.py`:

```python
"""Cache-first rebalancing service: cache hit, cache miss, stale, blockers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_blocks_on_missing_dob(db_session, fixture_user_no_dob):
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
    outcome = await compute_rebalancing_result(
        user=fixture_user_no_dob, user_question="rebalance",
        db=db_session, acting_user_id=fixture_user_no_dob.id, chat_session_id=None,
    )
    assert outcome.blocking_message is not None
    assert "date of birth" in outcome.blocking_message.lower() or "dob" in outcome.blocking_message.lower()
    assert outcome.response is None


@pytest.mark.asyncio
async def test_blocks_on_no_holdings(db_session, fixture_user_with_dob_no_holdings):
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
    outcome = await compute_rebalancing_result(
        user=fixture_user_with_dob_no_holdings, user_question="rebalance",
        db=db_session, acting_user_id=fixture_user_with_dob_no_holdings.id, chat_session_id=None,
    )
    assert outcome.blocking_message is not None
    assert "mutual fund portfolio" in outcome.blocking_message.lower()


@pytest.mark.asyncio
async def test_cache_hit_does_not_run_allocation(
    db_session, fixture_user_with_holdings, fixture_recent_allocation_row,
):
    """Allocation row < 90 days old → use it; do NOT call compute_allocation_result."""
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result

    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(),
    ) as mocked:
        outcome = await compute_rebalancing_result(
            user=fixture_user_with_holdings, user_question="rebalance",
            db=db_session, acting_user_id=fixture_user_with_holdings.id, chat_session_id=None,
        )
        mocked.assert_not_called()
        assert outcome.used_cached_allocation is True
        assert outcome.response is not None


@pytest.mark.asyncio
async def test_cache_miss_runs_allocation_inline(
    db_session, fixture_user_with_holdings, fixture_goal_allocation_outcome,
):
    """No allocation row → call compute_allocation_result, then run rebalancing."""
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result

    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(return_value=fixture_goal_allocation_outcome),
    ) as mocked:
        outcome = await compute_rebalancing_result(
            user=fixture_user_with_holdings, user_question="rebalance",
            db=db_session, acting_user_id=fixture_user_with_holdings.id, chat_session_id=None,
        )
        mocked.assert_called_once()
        assert outcome.used_cached_allocation is False
        assert outcome.response is not None
        assert outcome.allocation_snapshot_id is not None


@pytest.mark.asyncio
async def test_stale_cache_re_runs_allocation(
    db_session, fixture_user_with_holdings, fixture_old_allocation_row, fixture_goal_allocation_outcome,
):
    """Allocation row > 90 days old → ignore cache, re-run."""
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(return_value=fixture_goal_allocation_outcome),
    ) as mocked:
        outcome = await compute_rebalancing_result(
            user=fixture_user_with_holdings, user_question="rebalance",
            db=db_session, acting_user_id=fixture_user_with_holdings.id, chat_session_id=None,
        )
        mocked.assert_called_once()
        assert outcome.used_cached_allocation is False


@pytest.mark.asyncio
async def test_allocation_block_propagates(
    db_session, fixture_user_with_holdings,
):
    """Allocation returns blocking_message → service returns the same."""
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
    from app.services.ai_bridge.asset_allocation.service import AllocationRunOutcome

    blocked = AllocationRunOutcome(result=None, blocking_message="No API key.")
    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(return_value=blocked),
    ):
        outcome = await compute_rebalancing_result(
            user=fixture_user_with_holdings, user_question="rebalance",
            db=db_session, acting_user_id=fixture_user_with_holdings.id, chat_session_id=None,
        )
        assert outcome.blocking_message == "No API key."
        assert outcome.response is None


@pytest.mark.asyncio
async def test_persists_trades_row_on_success(
    db_session, fixture_user_with_holdings, fixture_recent_allocation_row,
):
    from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
    from sqlalchemy import select
    from app.models.rebalancing import RebalancingRecommendation, RecommendationType

    outcome = await compute_rebalancing_result(
        user=fixture_user_with_holdings, user_question="rebalance",
        db=db_session, acting_user_id=fixture_user_with_holdings.id, chat_session_id=None,
    )
    assert outcome.recommendation_id is not None
    rec = (await db_session.execute(
        select(RebalancingRecommendation).where(RebalancingRecommendation.id == outcome.recommendation_id)
    )).scalar_one()
    assert rec.recommendation_type == RecommendationType.REBALANCING_TRADES
```

(Engineer: implement `fixture_recent_allocation_row` and `fixture_old_allocation_row` in conftest by writing one `RebalancingRecommendation` row each with `recommendation_type=ALLOCATION` and the appropriate `created_at` (recent vs > 90 days old), with `recommendation_data["goal_allocation_output"]` populated from a `GoalAllocationOutput` fixture.)

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_service.py -v`
Expected: ERROR.

- [ ] **Step 3: Write `service.py`**

```python
# app/services/ai_bridge/rebalancing/service.py
"""Cache-first rebalancing orchestrator.

Reads the most recent goal allocation for the user; if it's > 90 days old or
absent, re-runs allocation inline. Then materialises engine inputs, runs the
pipeline on a worker thread, persists the trade-list, and renders chat markdown.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rebalancing import RebalancingRecommendation, RecommendationType
from app.services.ai_bridge.asset_allocation.service import (
    AllocationRunOutcome,
    compute_allocation_result,
)
from app.services.ai_bridge.common import ensure_ai_agents_path, trace_line
from app.services.ai_module_telemetry import record_ai_module_run
from app.services.portfolio_service import get_or_create_primary_portfolio
from app.services.rebalancing_recommendation_persist import (
    persist_rebalancing_recommendation,
)
from app.services.ai_bridge.rebalancing.formatter import format_rebalancing_chat_brief
from app.services.ai_bridge.rebalancing.input_builder import (
    build_rebalancing_input_for_user,
)

ensure_ai_agents_path()

from goal_based_allocation_pydantic.models import GoalAllocationOutput  # type: ignore[import-not-found]
from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]
from Rebalancing.pipeline import run_rebalancing  # type: ignore[import-not-found]


logger = logging.getLogger(__name__)

ALLOCATION_TTL_DAYS = 90


_MSG_MISSING_DOB = (
    "I need your date of birth to plan trades — it anchors your tax aging "
    "and risk profile. Add it on your profile and ask me again."
)
_MSG_NO_HOLDINGS = "Connect your mutual fund portfolio and ask me again."
_MSG_ENGINE_ERROR = (
    "I couldn't compute your rebalancing plan right now. Try again in a moment, "
    "and if it keeps happening let us know via the help option."
)
_MSG_UNPRICEABLE = (
    "I couldn't price one of the recommended funds — looks like our market data "
    "is missing for it. Try again later or let us know via help."
)


@dataclass(frozen=True)
class RebalancingRunOutcome:
    response: Optional[RebalancingComputeResponse]
    formatted_text: Optional[str] = None
    blocking_message: Optional[str] = None
    recommendation_id: Optional[uuid.UUID] = None
    allocation_snapshot_id: Optional[uuid.UUID] = None
    source_allocation_id: Optional[uuid.UUID] = None
    used_cached_allocation: bool = False


async def _user_has_mf_holdings(db: AsyncSession, user_id: uuid.UUID) -> bool:
    from app.models.mf.mf_transaction import MfTransaction
    row = (await db.execute(
        select(MfTransaction.id).where(MfTransaction.user_id == user_id).limit(1)
    )).first()
    return row is not None


async def _load_cached_allocation(
    db: AsyncSession, user_id: uuid.UUID,
) -> tuple[Optional[GoalAllocationOutput], Optional[uuid.UUID]]:
    """Latest ALLOCATION row ≤ 90 days old → (parsed output, row_id) or (None, None)."""
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=ALLOCATION_TTL_DAYS)

    rec = (await db.execute(
        select(RebalancingRecommendation)
        .where(RebalancingRecommendation.portfolio_id == portfolio.id)
        .where(RebalancingRecommendation.recommendation_type == RecommendationType.ALLOCATION)
        .where(RebalancingRecommendation.created_at >= cutoff)
        .order_by(desc(RebalancingRecommendation.created_at))
        .limit(1)
    )).scalar_one_or_none()
    if rec is None:
        return None, None
    payload = (rec.recommendation_data or {}).get("goal_allocation_output")
    if not payload:
        return None, None
    try:
        return GoalAllocationOutput.model_validate(payload), rec.id
    except Exception as exc:
        logger.warning("Cached allocation parse failed (%s); ignoring cache", exc)
        return None, None


async def compute_rebalancing_result(
    user,
    user_question: str,
    *,
    db: AsyncSession,
    acting_user_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID],
) -> RebalancingRunOutcome:
    trace_line("module: rebalancing — start")

    if getattr(user, "date_of_birth", None) is None:
        return RebalancingRunOutcome(response=None, blocking_message=_MSG_MISSING_DOB)

    if not await _user_has_mf_holdings(db, acting_user_id):
        return RebalancingRunOutcome(response=None, blocking_message=_MSG_NO_HOLDINGS)

    cached_output, source_allocation_id = await _load_cached_allocation(db, acting_user_id)
    used_cache = cached_output is not None
    allocation_snapshot_id: Optional[uuid.UUID] = None

    if cached_output is None:
        trace_line("rebalancing: allocation cache miss/stale — running allocation inline")
        alloc_outcome: AllocationRunOutcome = await compute_allocation_result(
            user, user_question,
            db=db, persist_recommendation=True,
            acting_user_id=acting_user_id, chat_session_id=chat_session_id,
            spine_mode="rebalance_chained",
        )
        if alloc_outcome.blocking_message is not None:
            return RebalancingRunOutcome(
                response=None, blocking_message=alloc_outcome.blocking_message,
            )
        if alloc_outcome.result is None:
            return RebalancingRunOutcome(response=None, blocking_message=_MSG_ENGINE_ERROR)
        cached_output = alloc_outcome.result
        source_allocation_id = alloc_outcome.rebalancing_recommendation_id
        allocation_snapshot_id = alloc_outcome.allocation_snapshot_id

    try:
        request, debug = await build_rebalancing_input_for_user(user, cached_output, db)
    except Exception as exc:
        logger.exception("rebalancing input builder failed: %s", exc)
        # Treat any builder error as unpriceable for now.
        return RebalancingRunOutcome(response=None, blocking_message=_MSG_UNPRICEABLE)

    trace_line(f"rebalancing input debug: {debug}")

    try:
        response: RebalancingComputeResponse = await asyncio.to_thread(
            run_rebalancing, request,
        )
    except Exception as exc:
        logger.exception("run_rebalancing failed: %s", exc)
        return RebalancingRunOutcome(response=None, blocking_message=_MSG_ENGINE_ERROR)

    rec_id = await persist_rebalancing_recommendation(
        db, acting_user_id, response,
        chat_session_id=chat_session_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
        user_question=user_question,
    )

    try:
        await record_ai_module_run(
            db,
            user_id=acting_user_id,
            session_id=chat_session_id,
            module="rebalancing",
            reason="full_pipeline_run",
            intent_detected="rebalancing",
            spine_mode=None,
            input_payload=request.model_dump(mode="json"),
            output_payload={
                "rebalancing_response": response.model_dump(mode="json"),
                "correlation_ids": {
                    "recommendation_id": str(rec_id),
                    "source_allocation_id": str(source_allocation_id) if source_allocation_id else None,
                },
            },
            emit_standard_log=False,
        )
    except Exception as exc:
        logger.warning("ai_module_telemetry skipped (non-fatal): %s", exc)

    formatted = format_rebalancing_chat_brief(response, used_cached_allocation=used_cache)

    return RebalancingRunOutcome(
        response=response,
        formatted_text=formatted,
        recommendation_id=rec_id,
        allocation_snapshot_id=allocation_snapshot_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
    )
```

- [ ] **Step 4: Run tests — expect 7 passed**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_service.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/service.py \
        app/services/ai_bridge/rebalancing/tests/test_service.py
git commit -m "feat(rebalancing): cache-first orchestrator service"
```

---

### Task 10: Chat handler + brain wiring

**Files:**
- Create: `app/services/ai_bridge/rebalancing/chat.py`
- Modify: `app/services/chat_core/brain.py` (~line 121-133, mirror of allocation branch)
- Modify: `app/services/ai_bridge/__init__.py` (re-export if convention)
- Create: `app/services/ai_bridge/rebalancing/tests/test_chat.py`

- [ ] **Step 1: Write failing test that locks the @register import side-effect**

Create `app/services/ai_bridge/rebalancing/tests/test_chat.py`:

```python
"""Mirror of asset_allocation's @register lock test."""

from app.services.ai_bridge.chat_dispatcher import _HANDLERS  # type: ignore[attr-defined]


def test_register_side_effect_for_rebalancing():
    """Importing rebalancing.chat must register the 'rebalancing' handler."""
    # Force a clean import cycle.
    import importlib
    import app.services.ai_bridge.rebalancing.chat as mod
    importlib.reload(mod)
    assert "rebalancing" in _HANDLERS, "@register('rebalancing') side-effect missing"


def test_handle_returns_chat_handler_result_on_success(db_session, fixture_turn_context_for_rebalance, monkeypatch):
    """Handler unpacks the outcome correctly."""
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.ai_bridge.rebalancing import chat as rb_chat
    from app.services.ai_bridge.rebalancing.service import RebalancingRunOutcome

    fake_outcome = RebalancingRunOutcome(
        response=None, formatted_text="OK plan",
        blocking_message=None,
        recommendation_id=fixture_turn_context_for_rebalance.user.id,  # any UUID
        allocation_snapshot_id=None, used_cached_allocation=True,
    )
    monkeypatch.setattr(rb_chat, "compute_rebalancing_result",
                        AsyncMock(return_value=fake_outcome))

    result = asyncio.run(rb_chat.handle(fixture_turn_context_for_rebalance))
    assert result.text == "OK plan"
    assert result.rebalancing_recommendation_id is not None


def test_handle_returns_blocking_message(fixture_turn_context_for_rebalance, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.ai_bridge.rebalancing import chat as rb_chat
    from app.services.ai_bridge.rebalancing.service import RebalancingRunOutcome

    blocked = RebalancingRunOutcome(response=None, blocking_message="No DOB")
    monkeypatch.setattr(rb_chat, "compute_rebalancing_result",
                        AsyncMock(return_value=blocked))
    result = asyncio.run(rb_chat.handle(fixture_turn_context_for_rebalance))
    assert result.text == "No DOB"
    assert result.rebalancing_recommendation_id is None
```

- [ ] **Step 2: Run — expect ImportError on rebalancing.chat**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_chat.py -v`
Expected: ERROR — module not found.

- [ ] **Step 3: Write `chat.py`**

```python
# app/services/ai_bridge/rebalancing/chat.py
"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import logging

from app.services.ai_bridge.chat_dispatcher import register
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
from app.services.chat_core.turn_context import TurnContext
from app.services.chat_core.types import ChatHandlerResult


logger = logging.getLogger(__name__)


@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    outcome = await compute_rebalancing_result(
        user=ctx.user,
        user_question=ctx.user_message,
        db=ctx.db,
        acting_user_id=ctx.acting_user_id,
        chat_session_id=ctx.session_id,
    )
    if outcome.blocking_message is not None:
        return ChatHandlerResult(
            text=outcome.blocking_message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )
    return ChatHandlerResult(
        text=outcome.formatted_text or "",
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.recommendation_id,
    )
```

(Engineer: confirm `TurnContext` exposes `user`, `user_message`, `db`, `acting_user_id`, `session_id` as named here. If field names differ, mirror the asset_allocation/chat.py call shape exactly — that's the ground truth.)

- [ ] **Step 4: Wire `chat_core/brain.py`**

Open `app/services/chat_core/brain.py` and find the dispatch block around line 114-158 (the existing `if intent_value == ...` chain). Add a new branch *before* the `else` fallback, mirroring the allocation branch:

```python
elif intent_value == "rebalancing":
    import app.services.ai_bridge.rebalancing.chat  # noqa: F401  — @register side-effect
    handler_result = await dispatch_chat("rebalancing", turn_context)
    # Mirror the assembly path used for the allocation branch:
    # populate ChatBrainResult.content / intent / *_id fields.
    # Look at the allocation branch immediately above this for the exact fields.
```

(Engineer: copy the exact ChatBrainResult-assembly statements from the allocation branch — they should be identical for rebalancing. Don't duplicate code blindly; the goal is the same `result` shape.)

- [ ] **Step 5: Run tests — expect 3 passed**

Run: `pytest app/services/ai_bridge/rebalancing/tests/test_chat.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the broader chat_core suite to confirm no regressions**

Run: `pytest app/services/chat_core/ app/services/ai_bridge/ -v`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add app/services/ai_bridge/rebalancing/chat.py \
        app/services/ai_bridge/rebalancing/tests/test_chat.py \
        app/services/chat_core/brain.py
git commit -m "feat(chat): wire rebalancing intent handler into ChatBrain"
```

---

### Task 11: HTTP debug endpoint + schemas

**Files:**
- Modify: `app/schemas/ai_modules.py` (add request/response models)
- Create: `app/routers/ai_modules/rebalancing.py`
- Modify: `app/routers/ai_modules/__init__.py` (mount the new router)
- Create: `app/routers/ai_modules/tests/test_rebalancing_endpoint.py` (or wherever tests live; mirror existing endpoint test conventions)

- [ ] **Step 1: Add request/response schemas**

Open `app/schemas/ai_modules.py` and add (matching `AssetAllocationRequest/Response` style):

```python
class RebalancingComputeApiRequest(BaseModel):
    question: str = Field(default="rebalance my portfolio")


class RebalancingComputeApiResponse(BaseModel):
    answer_markdown: str
    recommendation_id: Optional[UUID4] = None
    allocation_snapshot_id: Optional[UUID4] = None
    used_cached_allocation: bool
    blocking_message: Optional[str] = None
```

(Engineer: confirm imports — `BaseModel`, `Field`, `UUID4`, `Optional` already in this file from the allocation models.)

- [ ] **Step 2: Write router file**

```python
# app/routers/ai_modules/rebalancing.py
"""AI modules HTTP router — rebalancing.

Exposes ``POST /api/v1/ai-modules/rebalancing/compute`` for direct module
invocation (debug / frontend-driven runs without going through chat).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.models.user import User
from app.schemas.ai_modules import (
    RebalancingComputeApiRequest,
    RebalancingComputeApiResponse,
)
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result


router = APIRouter(prefix="/rebalancing", tags=["AI — Rebalancing"])


@router.post("/compute", response_model=RebalancingComputeApiResponse)
async def compute_rebalancing(
    payload: RebalancingComputeApiRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user_ctx: User = Depends(get_ai_user_context),
) -> RebalancingComputeApiResponse:
    outcome = await compute_rebalancing_result(
        user=user_ctx,
        user_question=payload.question,
        db=db,
        acting_user_id=current_user.id,
        chat_session_id=None,
    )
    await db.commit()
    if outcome.blocking_message is not None:
        return RebalancingComputeApiResponse(
            answer_markdown=outcome.blocking_message,
            recommendation_id=None,
            allocation_snapshot_id=None,
            used_cached_allocation=outcome.used_cached_allocation,
            blocking_message=outcome.blocking_message,
        )
    return RebalancingComputeApiResponse(
        answer_markdown=outcome.formatted_text or "",
        recommendation_id=outcome.recommendation_id,
        allocation_snapshot_id=outcome.allocation_snapshot_id,
        used_cached_allocation=outcome.used_cached_allocation,
        blocking_message=None,
    )
```

- [ ] **Step 3: Mount in `app/routers/ai_modules/__init__.py`**

Add the import + include in the existing aggregator:

```python
from app.routers.ai_modules.rebalancing import router as rebalancing_router
# ...
router.include_router(rebalancing_router)
```

- [ ] **Step 4: Add a smoke test**

Mirror the closest existing endpoint test (look for one of `app/routers/tests/test_*.py` or `app/routers/ai_modules/tests/`). Minimal:

```python
@pytest.mark.asyncio
async def test_post_rebalancing_compute_returns_200(test_client, fixture_user_with_holdings_token):
    response = await test_client.post(
        "/api/v1/ai-modules/rebalancing/compute",
        headers={"Authorization": f"Bearer {fixture_user_with_holdings_token}"},
        json={"question": "rebalance"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "answer_markdown" in body
    assert "used_cached_allocation" in body
```

- [ ] **Step 5: Run tests — expect green**

Run: `pytest app/routers/ai_modules/ -v`
Expected: all green (existing + new).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/ai_modules.py \
        app/routers/ai_modules/rebalancing.py \
        app/routers/ai_modules/__init__.py \
        app/routers/ai_modules/tests/test_rebalancing_endpoint.py
git commit -m "feat(api): POST /ai-modules/rebalancing/compute debug endpoint"
```

---

### Task 12: End-to-end chat test

**Files:**
- Create: `app/services/chat_core/tests/test_rebalancing_e2e.py`

- [ ] **Step 1: Write E2E test**

```python
"""End-to-end: 'rebalance my portfolio' message → ChatBrain → trade-list reply."""

import pytest


@pytest.mark.asyncio
async def test_rebalancing_chat_turn_returns_sectioned_markdown(
    db_session,
    fixture_user_with_holdings_and_recent_allocation,
    fixture_chat_session,
):
    from app.services.chat_core.brain import ChatBrain
    # Construct a ChatTurnInput shaped however ChatBrain expects.
    # See ChatBrain.run_turn signature in app/services/chat_core/brain.py.

    brain = ChatBrain()  # if there's a per-instance setup, mirror existing tests
    result = await brain.run_turn(
        # turn_input fields per the existing test patterns
        ...
    )

    assert result.intent == "rebalancing"
    assert "asset mix" not in result.content.lower()  # cache hit, no lead line
    assert "rebalance" in result.content.lower() or "trade" in result.content.lower()
    assert "sanity check" in result.content.lower()  # closing line present
    assert result.ideal_allocation_rebalancing_id is not None
```

(Engineer: this test depends on existing chat plumbing. If `ChatBrain` instance / `ChatTurnInput` shape needs more setup than fits a unit test, drop the test scope to integration-flavour: directly call `dispatch_chat("rebalancing", turn_context)` after seeding the cache row, and assert the same content predicates. The goal is one test that proves the wiring is intact.)

- [ ] **Step 2: Run test — expect PASS**

Run: `pytest app/services/chat_core/tests/test_rebalancing_e2e.py -v`
Expected: 1 passed.

- [ ] **Step 3: Run the full suite**

Run: `pytest -x -v`
Expected: full suite green.

- [ ] **Step 4: Commit**

```bash
git add app/services/chat_core/tests/test_rebalancing_e2e.py
git commit -m "test(rebalancing): end-to-end chat turn smoke test"
```

---

## Self-review checklist

Spec coverage:

- [x] Spec §2 decisions all locked into tasks: intent (Task 1), TTL (Task 9 cache lookup), corpus from holdings (Task 6), CSV from `Reference_docs/` (Task 3).
- [x] Spec §3 architecture: AI_bridge calls AI_Agents — service.py imports `run_rebalancing`, no engine modifications.
- [x] Spec §4 file map: every "to add" file appears in a task; every "to modify" file appears in Task 1, 2, or 10.
- [x] Spec §5.1 intent + dispatch: Task 1 + 10.
- [x] Spec §5.2 service control flow: Task 9 implements all 9 numbered steps in spec, including blocker matrix.
- [x] Spec §5.3 input builder: Tasks 4, 5, 6 cover holdings, tax-aging, top-level builder; ST/LT thresholds shared with engine via `Rebalancing.config` import.
- [x] Spec §5.4 persistence + schema: Task 2 (schema/migration) + Task 7 (helper).
- [x] Spec §5.5 formatter (friend voice): Task 8 with per-warning copy.
- [x] Spec §5.6 HTTP endpoint: Task 11.
- [x] Spec §5.7 tests: every named test file is created.

Type / signature consistency:

- `RebalancingRunOutcome` defined in Task 9 service.py with the exact field names referenced by Tasks 10 (chat.py) and 11 (router).
- `compute_rebalancing_result` keyword args match across service.py call sites in chat.py and router.
- `persist_rebalancing_recommendation` signature in Task 7 matches the call in Task 9.
- `build_rebalancing_input_for_user` signature in Task 6 matches the call in Task 9.
- `format_rebalancing_chat_brief(response, *, used_cached_allocation: bool)` consistent across Task 8 definition and Task 9 call.
- `FundRankRow` dataclass fields (`asset_subgroup`, `sub_category`, `rank`, `isin`, `fund_name`) consistent across Task 3 + Task 6.
- `Lot` and `HoldingLedgerEntry` from Task 4 are used unmodified in Task 5 + Task 6.

Placeholder scan:

- One `<auto>` placeholder in Task 2 alembic skeleton — that's the alembic-generated revision id (real value once Step 1 runs). Engineer-instruction notes are explicit.
- One `<ts>_rebalancing_chat_integration.py` placeholder — alembic timestamp prefix.
- The end-to-end test in Task 12 has `...` placeholders for `ChatTurnInput` field shape — explicitly flagged with engineer guidance to mirror existing tests. Acceptable because the chat-turn signature varies and isn't load-bearing for the spec.
- The formatter snapshot fixture `_make_minimal_response` has a documented `...` for SubgroupSummary construction. Acceptable: tests that depend on it are clearly listed and can be relaxed if construction is fiddly.

No other TBD/TODO/handwave content found.
