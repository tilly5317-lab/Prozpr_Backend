# Hybrid-Spine Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-05-04
**Status:** Plan
**Owner:** Amoul
**Supersedes:** none — this is the first migration off the per-module `_detect_action` pattern.
**Outcome of:** the 4-round adversarial debate (`.superpowers/brainstorm/85734-1777812185/content/arch-verdict.html`) and the spec discussion at `2026-04-28-unified-chat-modules-design.md`.

**Goal:** Replace the SQLAlchemy User-attribute monkey-patch + per-module `_detect_action` classifiers with an explicit `TurnContext.chat_overrides` field, a shared classifier helper, an `awaiting_save` cross-turn state column, and a `compliance_check` audit trail — without changing engine behavior.

**Architecture:** Hybrid spine on the existing `langchain-anthropic` stack (project convention forbids raw `anthropic` SDK). Per-turn read state lives in the existing frozen `TurnContext`, extended with an immutable `chat_overrides` field. Cross-turn write state (currently zero) becomes a new `chat_session_state` row. SEBI-defensible audit lives in a new `compliance_check` table referencing a versioned `prompt_versions` registry. Engines (`asset_allocation`, `Rebalancing`, future `goal_planning`) stay pure Python.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x async, PostgreSQL, Alembic, `langchain-anthropic` (`ChatAnthropic`), Pydantic 2.x, pytest.

---

## Summary

Today's chat architecture has four bleeding edges, all named in the verdict:

1. **User monkey-patching** at `app/services/ai_bridge/asset_allocation/chat.py:578-594` — `_apply_overrides` calls `setattr(user, "_chat_*_override", val)` on the SQLAlchemy User instance, then `_clear_overrides` calls `delattr(...)`. If anything between those raises, or if `session.flush()` interleaves, the transient chat override leaks onto the ORM-tracked instance.
2. **`_detect_action` duplication** between `asset_allocation/chat.py:652` and `rebalancing/chat.py:639`. Two near-identical Haiku calls with two near-identical structured-output schemas. As tax-planning and goal-planning modules land, this becomes 4-5 classifiers diverging under prompt drift.
3. **Implicit save-state.** `save_last_counterfactual` (chat.py:154-159) is gated by an LLM re-inferring "the immediately preceding turn was a counterfactual_explore" from chat history. Hallucination surface attached to a write path.
4. **Static prompts in code** mean a prompt fix ships as a code deploy. SEBI-relevant guardrails live in source files with no per-turn audit log of which rule was evaluated for which response.

This plan ships four PRs that close each bleed, in fixed sequence:

| PR | What | Why first/next |
|---|---|---|
| 1 | `TurnContext.chat_overrides` replaces User monkey-patch | Open correctness bug. Ships first because it's a hazard while any other change is in flight. |
| 2 | Shared classifier helper extracts the LLM-call mechanics from per-module `_detect_action` | Cleans up the divergence trajectory before more modules land. |
| 3 | `chat_session_state.awaiting_save` column + state-machine guard | Removes the rediscover-from-history hallucination at the save boundary. |
| 4 | `compliance_check` + `prompt_versions` tables, wired through `format_with_telemetry` | Last because it depends on the consolidated classifier (PR 2) and the awaiting-state primitive (PR 3) to have stable rule predicates. |

After PR 4, no new engine tools land until the migration completes. Each PR is independently shippable and revertable.

## Goals

- Eliminate User monkey-patching at `chat.py:578-594` and the seven `_chat_*_override` getattr sites in `input_builder.py:190-215`.
- Unify the per-module classifier mechanics in one helper without merging the per-module action schemas (asset_allocation has 7 modes; rebalancing has different modes; the schemas stay typed-per-module).
- Make `awaiting_save` an explicit boolean column read by the dispatcher, set by the engine.
- Add a `compliance_check` row per chat-turn rule evaluation, referencing a versioned `prompt_versions` row, queryable in SQL.
- Land each PR with a failing test that reproduces the bleed before any production code changes (TDD).
- Keep `langchain-anthropic` as the only LLM-call surface (per `Prozpr_Backend/CLAUDE.md`).

## Non-goals

- **SessionSummarizer / cross-session multi-year compaction.** Honestly conceded as research-grade in the verdict (`arch-verdict.html` § "What Hybrid is NOT"). Tracked in the *Out of scope* section below; no work in this plan.
- **`goal_planning` carve-out as a LangGraph subgraph.** No `goal_planning` handler exists today (`asset_allocation/chat.py` line 21: *"the goal_planning intent is handled in `app/services/chat_core/brain.py` via a canned redirect (no agent module exists for goal_planning yet)"*). Carve-out criteria are documented in the *Forward-looking criteria* section for trigger-time review; no code lands here.
- **22-tool tripwire as CI gate.** Operational discipline only — see *Operational checklist* at the end of this plan. No code.
- **Migration off `langchain-anthropic`.** Forbidden by project convention; the Hybrid spirit (native tool-call loop, 2 LLM calls/turn) is achieved via `ChatAnthropic.bind_tools()`, not via the raw `anthropic` SDK.
- **Rewriting any AI agent under `AI_Agents/src/`.** Engines stay untouched. Only `app/services/ai_bridge/` and `app/services/chat_core/` change, plus three new tables.
- **Building the LangGraph spine itself.** Hybrid is the spine; LangGraph is the reserved escape hatch for genuinely-deep flows once the carve-out criteria fire.

## Locked design decisions

| Decision | Choice | Why |
|---|---|---|
| LLM SDK | Continue `langchain-anthropic` via `ChatAnthropic.bind_tools()` for any tool-loop work | Existing project convention forbids raw `anthropic.messages.create` (per `Prozpr_Backend/CLAUDE.md`). |
| Override mechanism | New `TurnContext.chat_overrides: dict[str, Any] \| None` field | Frozen-dataclass field. Per-turn read-only. Replaces seven `_chat_*_override` User attributes with one frozen dict. |
| Action schema reuse | Keep `ChatAction` (asset_allocation) and `RebalanceAction` (rebalancing) as separate Pydantic models | Their mode spaces differ; merging them would lose typed precision. The shared helper takes a generic Pydantic class. |
| Cross-turn state | New `chat_session_state` table, one row per `chat_session_id`, upserted | Needed for `awaiting_save` (PR 3); future-proof for any other cross-turn slot. |
| Compliance audit | New `compliance_check` table with FK to `prompt_versions` | One row per (turn_id, rule_id, fired_bool, output_hash). SQL-queryable, survives framework version bumps. |
| Prompt versioning | New `prompt_versions` table; active prompts hashed & registered at app boot | Decouples prompt iteration from code deploy; gives `compliance_check` a stable FK. |
| Migration order | PR1 → PR2 → PR3 → PR4, each independently revertable | PR4 depends on PR2's rule predicates and PR3's awaiting-state primitive; PR3 depends on PR1's clean override path; PR2 can land before or after PR1 mechanically but PR1's bug is open in production-shaped code. |
| LangGraph carve-out | Documented criteria only, no code | No deep flow exists yet. |
| Tripwire | Operational checklist line, not CI | Karpathy guideline 2: no speculative tooling. |

## Architecture overview

```
ChatBrain.run_turn(turn)
│
├── build_turn_context(turn)
│       ├── load chat_session_state.awaiting_save        (NEW PR 3)
│       ├── load last_agent_runs per module
│       └── return TurnContext(... awaiting_save: bool, chat_overrides: None)
│
├── classify_user_message(...)               # unchanged
│
└── dispatch by intent (chat_dispatcher) ───────►  per-module handler
                                                    │
                                                    ├── intent_router.classify(ctx, prompt, ChatAction|RebalanceAction)
                                                    │       (shared LLM call mechanics — NEW PR 2)
                                                    │
                                                    ├── on counterfactual_explore:
                                                    │       new TurnContext(... chat_overrides=overrides)
                                                    │       (NEW PR 1, replaces _apply_overrides)
                                                    │       compute_engine(new_ctx)
                                                    │       upsert chat_session_state.awaiting_save = True (NEW PR 3)
                                                    │
                                                    ├── on save_last_counterfactual:
                                                    │       guard: ctx.awaiting_save (NEW PR 3, replaces history-inference)
                                                    │
                                                    └── format_with_telemetry(...)
                                                            └── per-rule compliance_check row write (NEW PR 4)
```

## File-structure changes

**Created:**
- `app/services/ai_bridge/intent_router.py` — shared classifier helper (PR 2).
- `app/models/chat_session_state.py` — cross-turn state row (PR 3).
- `app/models/compliance.py` — `PromptVersion`, `ComplianceCheck` (PR 4).
- `app/services/ai_bridge/compliance/__init__.py` — rule registry (PR 4).
- `app/services/ai_bridge/compliance/rules.py` — initial SEBI rule predicates (PR 4).
- `alembic/versions/2026_05_04_a_chat_session_state.py` — migration (PR 3).
- `alembic/versions/2026_05_04_b_compliance_audit.py` — migration (PR 4).

**Modified:**
- `app/services/chat_core/turn_context.py` — add `chat_overrides`, `awaiting_save` fields (PR 1, PR 3).
- `app/services/ai_bridge/asset_allocation/chat.py` — delete `_apply_overrides`/`_clear_overrides`/`_OVERRIDE_KEY_TO_USER_ATTR`/`_ainvoke`/`_detect_action`; replace with new helpers (PR 1, PR 2, PR 3).
- `app/services/ai_bridge/asset_allocation/input_builder.py` — replace `getattr(user, "_chat_*_override", None)` reads with `effective_param(ctx, key)` helper (PR 1).
- `app/services/ai_bridge/rebalancing/chat.py` — replace `_detect_rebal_action` body and remove its private `_ainvoke` (PR 2).
- `app/services/ai_bridge/answer_formatter/formatter.py` — extend `format_with_telemetry` to write compliance_check rows (PR 4).
- `app/models/__init__.py` — register new models (PR 3, PR 4).
- `app/services/chat_core/brain.py` — pass `db` into `build_turn_context` (already does); add the awaiting-state set on counterfactual-engine return (PR 3).

---

# PR 1: Replace User monkey-patch with `TurnContext.chat_overrides`

**Goal:** Eliminate the seven `setattr(user, "_chat_*_override", val)` mutations on the SQLAlchemy User instance. Replace with an immutable `chat_overrides: dict[str, Any] | None` field on `TurnContext`.

**Why first:** `_apply_overrides` mutates an ORM-tracked instance. Any `session.flush()` between override-set and override-clear leaks the chat-only state to the persisted user row. This is a correctness bug under load, not a stylistic one.

**Files:**
- Modify: `app/services/chat_core/turn_context.py` (add field)
- Create: `app/services/ai_bridge/asset_allocation/overrides.py` (new tiny module — `with_chat_overrides`, `effective_param`, `_ALLOWED_OVERRIDE_KEYS`, imported by both `chat.py` and `input_builder.py` so neither imports from the other; avoids the `chat.py → service.py → input_builder.py → chat.py` cycle)
- Modify: `app/services/ai_bridge/asset_allocation/input_builder.py:190-215` (replace getattr reads with `effective_param` import from the new `overrides` module)
- Modify: `app/services/ai_bridge/asset_allocation/chat.py:88-98, 569-594, 478-503 area` (delete monkey-patch, thread context, import helpers from new `overrides` module)
- Test: `app/services/chat_core/tests/test_turn_context.py` (new test for chat_overrides)
- Test: `app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py` (new file)

### Task 1.1: Write the failing test for the leak

- [ ] **Step 1: Create the test file**

```python
# app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py
"""Regression test: counterfactual override must NOT leak to the User instance."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.ai_bridge.asset_allocation import chat as aa_chat
from app.services.chat_core.turn_context import TurnContext


@pytest.mark.asyncio
async def test_counterfactual_override_does_not_mutate_user_instance(
    user_with_goals: User, db: AsyncSession,
) -> None:
    """The override path must thread overrides through TurnContext, not setattr on User."""
    ctx = TurnContext(
        user_ctx=user_with_goals,
        user_question="what if my risk were 7?",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=db,
        effective_user_id=user_with_goals.id,
        last_agent_runs={},
        active_intent="asset_allocation",
        chat_overrides=None,  # NEW field; default None
    )

    # Sanity: no _chat_*_override attrs before the run
    for attr in (
        "_chat_risk_score_override",
        "_chat_total_corpus_override",
        "_chat_additional_cash_override",
        "_chat_annual_income_override",
        "_chat_monthly_expense_override",
        "_chat_emergency_fund_needed_override",
        "_chat_tax_regime_override",
    ):
        assert not hasattr(user_with_goals, attr), \
            f"User instance has stale {attr} before the test even ran"

    # Run a counterfactual_explore with an override
    overrides = {"effective_risk_score": 7}
    new_ctx = aa_chat.with_chat_overrides(ctx, overrides)

    # The User instance must NOT have any _chat_*_override attrs
    for attr in (
        "_chat_risk_score_override",
        "_chat_total_corpus_override",
        "_chat_additional_cash_override",
        "_chat_annual_income_override",
        "_chat_monthly_expense_override",
        "_chat_emergency_fund_needed_override",
        "_chat_tax_regime_override",
    ):
        assert not hasattr(user_with_goals, attr), \
            f"PR 1 regression: User mutated with {attr}"

    # The new ctx carries the override
    assert new_ctx.chat_overrides == {"effective_risk_score": 7}

    # The original ctx is unchanged (frozen dataclass)
    assert ctx.chat_overrides is None
```

- [ ] **Step 2: Run it. Expect failure.**

```bash
cd Prozpr_Backend && pytest app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py -v
```

Expected: `AttributeError: 'TurnContext' object has no attribute 'chat_overrides'` (the frozen dataclass doesn't have the field yet).

- [ ] **Step 3: Commit the failing test**

```bash
git add app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py
git commit -m "test(aa): add failing regression for User-monkey-patch leak"
```

### Task 1.2: Add `chat_overrides` to `TurnContext`

- [ ] **Step 1: Modify `app/services/chat_core/turn_context.py`** — add the field to the frozen dataclass.

```python
# In TurnContext, after `active_intent`:
    chat_overrides: dict[str, Any] | None = None
    """Per-turn chat overrides keyed by ChatAction.overrides keys.
    Replaces the legacy User._chat_*_override monkey-patch (PR 1). Read-only.
    A turn that needs different overrides constructs a new TurnContext via
    dataclasses.replace().
    """
```

- [ ] **Step 2: Modify `build_turn_context` to default `chat_overrides=None`**

```python
# turn_context.py — in the return statement:
    return TurnContext(
        user_ctx=turn.user_ctx,
        user_question=turn.user_question,
        conversation_history=turn.conversation_history,
        client_context=turn.client_context,
        session_id=turn.session_id,
        db=turn.db,
        effective_user_id=turn.effective_user_id,
        last_agent_runs=last_runs,
        active_intent=active_intent,
        chat_overrides=None,
    )
```

- [ ] **Step 3: Run the regression test. Confirm it now fails on a different line**

```bash
pytest app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py -v
```

Expected: `AttributeError: module ... has no attribute 'with_chat_overrides'` — the helper doesn't exist yet.

- [ ] **Step 4: Commit**

```bash
git add app/services/chat_core/turn_context.py
git commit -m "feat(turn_context): add chat_overrides frozen field"
```

### Task 1.3: Add `with_chat_overrides` helper + `effective_param` reader in a new `overrides.py` module

> **Why a new module instead of putting these in `chat.py`:** `input_builder.py` needs to call `effective_param` (Task 1.4). But `chat.py → service.py → input_builder.py` is the existing import chain. Adding `input_builder.py → chat.py` closes a cycle at module-load time. The new `overrides.py` is a leaf module both files can safely import.

- [ ] **Step 1: Create `app/services/ai_bridge/asset_allocation/overrides.py`**

```python
"""Per-turn chat override helpers.

This module exists as a leaf — neither `chat.py` nor `input_builder.py`
imports the other, but both import from here. Replaces the legacy
User._chat_*_override monkey-patch (PR 1).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from app.services.chat_core.turn_context import TurnContext


_ALLOWED_OVERRIDE_KEYS = frozenset({
    "effective_risk_score",
    "total_corpus",
    "additional_cash_inr",
    "annual_income",
    "monthly_household_expense",
    "emergency_fund_needed",
    "tax_regime",
})


def with_chat_overrides(
    ctx: TurnContext, overrides: dict[str, Any] | None,
) -> TurnContext:
    """Return a new TurnContext with chat_overrides set. The original is unchanged."""
    return dataclasses.replace(ctx, chat_overrides=overrides or None)


def effective_param(
    ctx: TurnContext, key: str, fallback: Any,
) -> Any:
    """Return chat_overrides[key] if present, else fallback.

    `key` must be in the allow-list; an unknown key raises ValueError so a typo
    in input_builder cannot silently shadow a saved value.
    """
    if key not in _ALLOWED_OVERRIDE_KEYS:
        raise ValueError(f"effective_param: unknown override key {key!r}")
    if ctx.chat_overrides is None:
        return fallback
    if key not in ctx.chat_overrides:
        return fallback
    return ctx.chat_overrides[key]
```

- [ ] **Step 2: Add the import to `chat.py`** (near other ai_bridge imports — does NOT import from `input_builder`):

```python
from app.services.ai_bridge.asset_allocation.overrides import (
    _ALLOWED_OVERRIDE_KEYS,
    effective_param,  # used only by tests via re-export; chat.py itself uses it via input_builder
    with_chat_overrides,
)
```

- [ ] **Step 3: Update the regression test** to import from the new module instead of `chat`:

```python
# In test_chat_overrides.py:
from app.services.ai_bridge.asset_allocation.overrides import with_chat_overrides
# (replaces: aa_chat.with_chat_overrides — though aa_chat re-exports it for compatibility)
```

- [ ] **Step 4: Run the regression test. Expected: PASS** (asserts on User attrs and chat_overrides shape).

```bash
pytest app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/overrides.py app/services/ai_bridge/asset_allocation/chat.py app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py
git commit -m "feat(aa): overrides.py module with with_chat_overrides + effective_param"
```

### Task 1.4: Switch `input_builder.py` reads from `getattr(user, "_chat_*", ...)` to `effective_param(ctx, ...)`

- [ ] **Step 1: Add a failing test for input_builder under override**

```python
# Add to app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py
@pytest.mark.asyncio
async def test_input_builder_reads_override_from_ctx_not_user(
    user_with_goals: User, db: AsyncSession,
) -> None:
    """input_builder must consult ctx.chat_overrides — not User attrs — for risk score."""
    from app.services.ai_bridge.asset_allocation.input_builder import (
        build_goal_allocation_input_for_user,
    )

    ctx = TurnContext(
        user_ctx=user_with_goals, user_question="x",
        conversation_history=[], client_context=None,
        session_id=uuid.uuid4(), db=db,
        effective_user_id=user_with_goals.id,
        last_agent_runs={}, active_intent="asset_allocation",
        chat_overrides={"effective_risk_score": 7},
    )

    inp = await build_goal_allocation_input_for_user(ctx)  # NEW SIGNATURE: ctx, not user

    assert inp.effective_risk_score == 7
    assert not hasattr(user_with_goals, "_chat_risk_score_override")
```

- [ ] **Step 2: Run. Expected fail** — `build_goal_allocation_input_for_user` still takes `user`, not `ctx`.

```bash
pytest app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py::test_input_builder_reads_override_from_ctx_not_user -v
```

- [ ] **Step 3: Modify `input_builder.py`**: change the signature from `(user, ...)` to `(ctx, ...)`. Replace each of the seven `getattr(user, "_chat_*_override", None)` calls with `effective_param(ctx, "<key>", <fallback>)`.

Concrete diff at `input_builder.py:190-215` (replace these lines exactly):

```python
# OLD:
    _risk_override = getattr(user, "_chat_risk_score_override", None)
# NEW:
    _risk_override = effective_param(ctx, "effective_risk_score", None)

# OLD:
    _corpus_override = getattr(user, "_chat_total_corpus_override", None)
# NEW:
    _corpus_override = effective_param(ctx, "total_corpus", None)

# OLD:
    _additional_cash = getattr(user, "_chat_additional_cash_override", None)
# NEW:
    _additional_cash = effective_param(ctx, "additional_cash_inr", None)

# OLD:
    _income_override = getattr(user, "_chat_annual_income_override", None)
# NEW:
    _income_override = effective_param(ctx, "annual_income", None)

# OLD:
    _expense_override = getattr(user, "_chat_monthly_expense_override", None)
# NEW:
    _expense_override = effective_param(ctx, "monthly_household_expense", None)

# OLD:
    _emergency_override = getattr(user, "_chat_emergency_fund_needed_override", None)
# NEW:
    _emergency_override = effective_param(ctx, "emergency_fund_needed", None)

# OLD:
    _tax_regime_override = getattr(user, "_chat_tax_regime_override", None)
# NEW:
    _tax_regime_override = effective_param(ctx, "tax_regime", None)
```

Add the import at the top of `input_builder.py`:

```python
from app.services.ai_bridge.asset_allocation.overrides import effective_param
```

(No circular import: `overrides.py` is a leaf module that only depends on `chat_core.turn_context`. Both `chat.py` and `input_builder.py` import from it.)

**Also update the function signature** at the top of the function (around `input_builder.py:170-190`):

```python
# OLD:
async def build_goal_allocation_input_for_user(user: User, ...) -> GoalAllocationInput:

# NEW:
async def build_goal_allocation_input_for_user(ctx: TurnContext, ...) -> GoalAllocationInput:
    user = ctx.user_ctx
    # remainder of function body unchanged except for the seven getattr → effective_param swaps below
```

Add the import for `TurnContext`:

```python
from app.services.chat_core.turn_context import TurnContext
```

- [ ] **Step 4: Run all asset_allocation tests**

```bash
pytest app/services/ai_bridge/asset_allocation/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/input_builder.py app/services/ai_bridge/asset_allocation/tests/test_chat_overrides.py
git commit -m "refactor(aa): input_builder reads overrides from ctx not User attrs"
```

### Task 1.5: Delete the monkey-patch and thread `chat_overrides` through the counterfactual handler

> **Scope note:** `compute_allocation_result(user, ...)` in `service.py` is also called by the standalone HTTP allocation endpoint (per `chat.py` docstring). To avoid breaking that caller, **its public signature stays the same**. The change is local to `build_goal_allocation_input_for_user`, which now takes `ctx`. `compute_allocation_result` constructs an empty-overrides ctx for non-chat callers.

- [ ] **Step 1: Delete `_OVERRIDE_KEY_TO_USER_ATTR`, `_apply_overrides`, `_clear_overrides`** from `chat.py:88-98, 569-594`.

- [ ] **Step 2: Modify the counterfactual call site.** Find the function that currently calls `_apply_overrides` (it's `_counterfactual_explore` near line 478). Replace this pattern:

```python
# OLD:
    _apply_overrides(ctx.user_ctx, overrides)
    try:
        outcome = await compute_allocation_result(ctx.user_ctx, ctx.user_question, ...)
    finally:
        _clear_overrides(ctx.user_ctx, overrides)
```

With:

```python
# NEW:
    overridden_ctx = with_chat_overrides(ctx, overrides)
    outcome = await compute_allocation_result(
        overridden_ctx.user_ctx, overridden_ctx.user_question, ...,
        chat_ctx=overridden_ctx,  # NEW kwarg, see Step 3
    )
```

- [ ] **Step 3: Add a `chat_ctx` kwarg to `compute_allocation_result`** in `service.py` — does not change the existing positional signature, so the standalone HTTP caller is unaffected.

```python
# In service.py:
async def compute_allocation_result(
    user: User,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
    chat_ctx: "TurnContext | None" = None,   # NEW
) -> AllocationComputeOutcome:
    ...
    # Construct an empty-overrides ctx if the caller didn't supply one.
    # This keeps non-chat callers (HTTP endpoint) unchanged.
    if chat_ctx is None:
        from app.services.chat_core.turn_context import TurnContext
        chat_ctx = TurnContext(
            user_ctx=user, user_question=user_question,
            conversation_history=[], client_context=None,
            session_id=uuid.uuid4(),  # synthetic; not persisted
            db=db, effective_user_id=acting_user_id or user.id,
            last_agent_runs={}, active_intent=None,
            chat_overrides=None,
        )
    inp = await build_goal_allocation_input_for_user(chat_ctx)
    # ... rest unchanged
```

- [ ] **Step 4: Update `_validate_overrides`** — it currently checks against `_OVERRIDE_KEY_TO_USER_ATTR` (now deleted). Replace with check against `_ALLOWED_OVERRIDE_KEYS`:

```python
from app.services.ai_bridge.asset_allocation.overrides import _ALLOWED_OVERRIDE_KEYS


def _validate_overrides(overrides: dict[str, Any]) -> bool:
    """All override keys must be in the allow-list."""
    return all(k in _ALLOWED_OVERRIDE_KEYS for k in overrides.keys())
```

- [ ] **Step 5: Run all asset_allocation + chat_core tests + the HTTP endpoint tests** (the HTTP endpoint signature didn't change, so they should still pass without edits):

```bash
pytest app/services/ai_bridge/asset_allocation/ app/services/chat_core/ app/routers/tests/ -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/chat.py app/services/ai_bridge/asset_allocation/service.py
git commit -m "refactor(aa): delete _apply/clear_overrides; thread chat_overrides via ctx"
```

### Task 1.6: Verify no dead getattr lookups remain

- [ ] **Step 1: Grep for stragglers**

```bash
grep -rn "_chat_.*_override" Prozpr_Backend/app/ AI_Agents/src/ 2>/dev/null
```

Expected output: empty (no matches).

- [ ] **Step 2: Run the full ai_bridge test suite**

```bash
pytest app/services/ai_bridge/ -v
```

Expected: all pass.

- [ ] **Step 3: Final commit (only if grep found stragglers and you fixed them — otherwise skip).**

---

# PR 2: Shared classifier helper extracts the LLM-call mechanics

**Goal:** Eliminate the duplicated `_detect_action` (asset_allocation) / `_detect_rebal_action` (rebalancing) LLM-call boilerplate. Both modules call into one `classify_action()` helper that takes a Pydantic action model + a system prompt + a snapshot block, returning a typed action.

**Why this PR (not full classifier merge):** Asset-allocation has 7 modes (`narrate`, `educate`, `counterfactual_explore`, `save_last_counterfactual`, `clarify`, `recompute_full`, `redirect`); rebalancing has different modes. Forcing one action schema would lose typed precision. The helper stays generic over `Type[BaseModel]`.

**Files:**
- Create: `app/services/ai_bridge/intent_router.py`
- Modify: `app/services/ai_bridge/asset_allocation/chat.py:652-682` (replace `_detect_action`)
- Modify: `app/services/ai_bridge/rebalancing/chat.py:639+` (replace `_detect_rebal_action`)
- Test: `app/services/ai_bridge/tests/test_intent_router.py` (new file)

### Task 2.1: Write the failing test

- [ ] **Step 1: Create `app/services/ai_bridge/tests/test_intent_router.py`**

```python
"""classify_action() — the shared classifier helper used by every chat module."""

from __future__ import annotations

from typing import Literal, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel


class _DummyAction(BaseModel):
    mode: Literal["narrate", "redirect"]
    redirect_reason: Optional[str] = None


@pytest.mark.asyncio
async def test_classify_action_calls_haiku_with_structured_output() -> None:
    from app.services.ai_bridge.intent_router import classify_action

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(
        return_value=_DummyAction(mode="redirect", redirect_reason="x"),
    )
    structured_llm = MagicMock(invoke=fake_llm.invoke)

    fake_chat_anthropic = MagicMock()
    fake_chat_anthropic.with_structured_output = MagicMock(return_value=structured_llm)

    with patch(
        "app.services.ai_bridge.intent_router.ChatAnthropic",
        return_value=fake_chat_anthropic,
    ):
        result = await classify_action(
            action_model=_DummyAction,
            system_prompt="route this",
            user_block="hello",
            api_key="dummy-key",
        )

    fake_chat_anthropic.with_structured_output.assert_called_once_with(_DummyAction)
    assert result.mode == "redirect"
    assert result.redirect_reason == "x"
```

- [ ] **Step 2: Run. Expected fail** — `intent_router` doesn't exist.

```bash
pytest app/services/ai_bridge/tests/test_intent_router.py -v
```

- [ ] **Step 3: Commit**

```bash
git add app/services/ai_bridge/tests/test_intent_router.py
git commit -m "test(intent_router): failing test for shared classifier helper"
```

### Task 2.2: Create `intent_router.py`

- [ ] **Step 1: Create the file**

```python
# app/services/ai_bridge/intent_router.py
"""Shared classifier mechanics for per-module chat handlers.

Each module supplies its own typed Pydantic action model + system prompt;
this helper does the langchain-anthropic Haiku call + structured-output binding.
Replaces the duplicated _detect_action / _detect_rebal_action LLM call paths.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Type, TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_TOKENS = 400


async def classify_action(
    *,
    action_model: Type[T],
    system_prompt: str,
    user_block: str,
    api_key: str,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> T:
    """Single Haiku call with structured output bound to `action_model`.

    Caller supplies the per-module prompt and user block (snapshot, history,
    question). Returns a typed instance of `action_model`.
    """
    llm = ChatAnthropic(
        model=model, api_key=api_key, max_tokens=max_tokens,
    ).with_structured_output(action_model)

    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_block),
    ]
    return await asyncio.to_thread(llm.invoke, messages)
```

- [ ] **Step 2: Run the test. Expected: PASS.**

```bash
pytest app/services/ai_bridge/tests/test_intent_router.py -v
```

- [ ] **Step 3: Commit**

```bash
git add app/services/ai_bridge/intent_router.py
git commit -m "feat(intent_router): shared classify_action helper"
```

### Task 2.3: Switch `asset_allocation/chat.py:_detect_action` to use `classify_action`

- [ ] **Step 1: Replace the body of `_detect_action`** at `chat.py:652-682`:

```python
async def _detect_action(
    last_alloc: AgentRunRecord, ctx: TurnContext,
) -> ChatAction:
    """One Haiku call returning a ChatAction. Uses shared classify_action."""
    from app.services.ai_bridge.intent_router import classify_action

    slim = _slim_snapshot(last_alloc.output_payload)
    snapshot_json = json.dumps(slim, default=str)
    if len(snapshot_json) > _DETECT_SNAPSHOT_BUDGET:
        logger.info(
            "detect_action_snapshot_truncated original_len=%d budget=%d",
            len(snapshot_json), _DETECT_SNAPSHOT_BUDGET,
        )
        snapshot_json = snapshot_json[:_DETECT_SNAPSHOT_BUDGET]

    history_block = build_detect_history_block(ctx.conversation_history)
    history_section = (
        f"\n\nRecent conversation (oldest → newest):\n{history_block}"
        if history_block else ""
    )
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Saved plan snapshot (slim):\n{snapshot_json}"
        f"{history_section}"
    )

    return await classify_action(
        action_model=ChatAction,
        system_prompt=_DETECT_SYSTEM,
        user_block=user_block,
        api_key=get_settings().get_anthropic_asset_allocation_key(),
    )
```

- [ ] **Step 2: Delete the now-unused `_ainvoke` helper** at `chat.py:743-751`. It only had one caller (`_detect_action`), now gone.

- [ ] **Step 3: Run asset_allocation tests**

```bash
pytest app/services/ai_bridge/asset_allocation/ -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/chat.py
git commit -m "refactor(aa): _detect_action delegates to shared classify_action"
```

### Task 2.4: Same change in `rebalancing/chat.py`

- [ ] **Step 1: Replace `_detect_rebal_action` body** at `rebalancing/chat.py:639+` to call `classify_action(action_model=RebalanceAction, system_prompt=<the rebalancing prompt>, user_block=..., api_key=get_settings().get_anthropic_rebalancing_key())`.

(Read the current body and replace with the same shape as Task 2.3 step 1, swapping `ChatAction → RebalanceAction` and the API-key getter.)

- [ ] **Step 2: Delete the rebalancing-side `_ainvoke` if it exists.**

```bash
grep -n "_ainvoke" Prozpr_Backend/app/services/ai_bridge/rebalancing/chat.py
```

If found, delete it.

- [ ] **Step 3: Run rebalancing tests**

```bash
pytest app/services/ai_bridge/rebalancing/ -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add app/services/ai_bridge/rebalancing/chat.py
git commit -m "refactor(rebal): _detect_rebal_action delegates to classify_action"
```

### Task 2.5: Verify duplication is gone

- [ ] **Step 1: Grep**

```bash
grep -rn "with_structured_output(ChatAction\|with_structured_output(RebalanceAction" Prozpr_Backend/app/services/
```

Expected: only one match (in `intent_router.py` if you bind there, otherwise zero — both modules now go through the shared helper).

- [ ] **Step 2: Full ai_bridge tests**

```bash
pytest app/services/ai_bridge/ -v
```

Expected: all pass.

---

# PR 3: `chat_session_state.awaiting_save` column + state-machine guard

**Goal:** Replace the LLM-rediscovers-from-history gate on `save_last_counterfactual` with an explicit boolean column on a new `chat_session_state` table.

**Why now:** PR 1 stabilized override flow; PR 2 stabilized classifier mechanics. Now we can replace the implicit gate without competing schema concerns.

**Files:**
- Create: `app/models/chat_session_state.py`
- Create: `alembic/versions/2026_05_04_a_chat_session_state.py`
- Modify: `app/models/__init__.py` (register `ChatSessionState`)
- Modify: `app/services/chat_core/turn_context.py` (load `awaiting_save`)
- Modify: `app/services/ai_bridge/asset_allocation/chat.py` (set + read awaiting_save)
- Modify: `app/services/ai_bridge/asset_allocation/chat.py:_DETECT_SYSTEM` (remove the "only emit `save_last_counterfactual` when the IMMEDIATELY PRECEDING turn was a counterfactual_explore" prose — replaced by explicit gate in code)
- Test: `app/services/chat_core/tests/test_chat_session_state.py` (new)

### Task 3.1: Add the model

- [ ] **Step 1: Create `app/models/chat_session_state.py`**

```python
"""SQLAlchemy ORM model — chat_session_state.

Per-session cross-turn state. One row per chat_session_id, upserted by chat
handlers. Today this carries `awaiting_save` (the typed replacement for the
implicit LLM-inferred save-gate). Future cross-turn slots — e.g. an active
counterfactual_run_id, or a per-module pending_proposal_id — go here too.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession


class ChatSessionState(Base):
    __tablename__ = "chat_session_state"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    awaiting_save: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_counterfactual_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_ai_module_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped["ChatSession"] = relationship()
```

- [ ] **Step 2: Register in `app/models/__init__.py`**

```python
# Add to the model imports:
from app.models.chat_session_state import ChatSessionState  # noqa: F401
```

- [ ] **Step 3: Commit**

```bash
git add app/models/chat_session_state.py app/models/__init__.py
git commit -m "feat(model): add ChatSessionState (awaiting_save, last_counterfactual_run_id)"
```

### Task 3.2: Alembic migration

- [ ] **Step 1: Generate the migration scaffold**

```bash
cd Prozpr_Backend && alembic revision -m "add_chat_session_state"
```

- [ ] **Step 2: Replace the generated body** with this concrete migration. File: `alembic/versions/<auto-rev-id>_add_chat_session_state.py`.

```python
"""Add chat_session_state table.

Revision ID: <auto>
Revises: f7c91d2e4a00  # last existing rev — verify with `alembic heads`
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "<filled-by-alembic>"
down_revision: Union[str, None] = "<previous-rev-from-alembic-heads>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_session_state",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("awaiting_save", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "last_counterfactual_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_ai_module_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.id"], ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_session_state")
```

- [ ] **Step 3: Run the migration locally**

```bash
alembic upgrade head
```

Expected: `INFO [alembic.runtime.migration] Running upgrade ... -> ..., add_chat_session_state`.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): add chat_session_state table"
```

### Task 3.3: Load `awaiting_save` in `build_turn_context`

- [ ] **Step 1: Add a failing test** in `app/services/chat_core/tests/test_chat_session_state.py`:

```python
"""TurnContext loads awaiting_save from chat_session_state."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatSession
from app.models.chat_session_state import ChatSessionState
from app.models.user import User
from app.services.chat_core.turn_context import build_turn_context
from app.services.chat_core.types import ChatTurnInput


@pytest.mark.asyncio
async def test_build_turn_context_loads_awaiting_save_true(
    user_with_goals: User, db: AsyncSession,
) -> None:
    session = ChatSession(user_id=user_with_goals.id)
    db.add(session)
    await db.flush()
    db.add(ChatSessionState(session_id=session.id, awaiting_save=True))
    await db.flush()

    turn = ChatTurnInput(
        user_ctx=user_with_goals,
        user_question="save it",
        conversation_history=[],
        client_context=None,
        session_id=session.id,
        db=db,
        effective_user_id=user_with_goals.id,
    )
    ctx = await build_turn_context(turn)
    assert ctx.awaiting_save is True


@pytest.mark.asyncio
async def test_build_turn_context_awaiting_save_defaults_false(
    user_with_goals: User, db: AsyncSession,
) -> None:
    session = ChatSession(user_id=user_with_goals.id)
    db.add(session)
    await db.flush()
    # No ChatSessionState row at all.

    turn = ChatTurnInput(
        user_ctx=user_with_goals, user_question="x",
        conversation_history=[], client_context=None,
        session_id=session.id, db=db,
        effective_user_id=user_with_goals.id,
    )
    ctx = await build_turn_context(turn)
    assert ctx.awaiting_save is False
```

- [ ] **Step 2: Run. Expected fail** — `TurnContext` has no `awaiting_save` field.

- [ ] **Step 3: Modify `turn_context.py`** to add the field and load it.

```python
# Add to TurnContext (after chat_overrides):
    awaiting_save: bool = False

# Add to build_turn_context body, inside the try block:
            awaiting_save = await _load_awaiting_save(turn.db, turn.session_id)

# In the return:
    return TurnContext(
        ...,
        chat_overrides=None,
        awaiting_save=awaiting_save,
    )

# New private function:
async def _load_awaiting_save(
    db: AsyncSession, session_id: uuid.UUID,
) -> bool:
    from app.models.chat_session_state import ChatSessionState
    stmt = select(ChatSessionState.awaiting_save).where(
        ChatSessionState.session_id == session_id,
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    return bool(row) if row is not None else False
```

- [ ] **Step 4: Run the test. Expected: PASS.**

```bash
pytest app/services/chat_core/tests/test_chat_session_state.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_core/turn_context.py app/services/chat_core/tests/test_chat_session_state.py
git commit -m "feat(turn_context): load awaiting_save from chat_session_state"
```

### Task 3.4: Replace history-inference gate with explicit ctx.awaiting_save

- [ ] **Step 1: Add a failing test** that asserts `save_last_counterfactual` requires `awaiting_save=True`.

```python
# In app/services/ai_bridge/asset_allocation/tests/ — new file test_save_gate.py:
@pytest.mark.asyncio
async def test_save_last_counterfactual_rejected_when_awaiting_save_false(
    user_with_alloc_run: User, db: AsyncSession,
) -> None:
    """Without awaiting_save=True, classifying as save_last_counterfactual must
    fall through to redirect — even if history seems to suggest a recent
    counterfactual. The gate is data, not LLM inference."""
    from app.services.ai_bridge.asset_allocation.chat import handle
    from app.services.chat_core.turn_context import TurnContext

    # Build a non-empty last_agent_runs from the persisted alloc run.
    from app.services.chat_core.turn_context import AgentRunRecord
    last_run = AgentRunRecord(
        id=uuid.uuid4(), module="asset_allocation",
        intent_detected="asset_allocation",
        input_payload=None,
        output_payload={"allocation_result": {}},  # truthy stub
        created_at=datetime.now(timezone.utc),
    )

    ctx = TurnContext(
        user_ctx=user_with_alloc_run, user_question="save it",
        conversation_history=[], client_context=None,
        session_id=uuid.uuid4(), db=db,
        effective_user_id=user_with_alloc_run.id,
        last_agent_runs={"asset_allocation": last_run},
        active_intent="asset_allocation",
        chat_overrides=None,
        awaiting_save=False,
    )
    result = await handle(ctx)
    # Should redirect, not save (the engine compute does not run).
    assert "save" not in result.text.lower() or "head to" in result.text.lower()
```

- [ ] **Step 2: Run. Expected fail.**

- [ ] **Step 3: Modify `_dispatch_action` in `chat.py`** at the `save_last_counterfactual` branch:

```python
    if action.mode == "save_last_counterfactual":
        if not ctx.awaiting_save:
            # The classifier guessed wrong. There is no pending counterfactual
            # to commit. Redirect rather than risk a write on stale state.
            return ChatHandlerResult(
                text=_REDIRECT_TEMPLATE.format(reason="confirm what to save"),
            )
        return await _save_last_counterfactual(ctx)
```

- [ ] **Step 4: After `_counterfactual_explore` returns** in the same dispatcher function — upsert `awaiting_save = True`:

```python
async def _counterfactual_explore(...):
    outcome = ...  # existing code
    # NEW: mark this session as awaiting save before returning.
    if ctx.db is not None and ctx.session_id is not None:
        await _upsert_awaiting_save(ctx.db, ctx.session_id, True)
    return ChatHandlerResult(text=...)
```

with helper:

```python
async def _upsert_awaiting_save(db: AsyncSession, session_id: uuid.UUID, value: bool) -> None:
    from app.models.chat_session_state import ChatSessionState
    from sqlalchemy.dialects.postgresql import insert
    stmt = insert(ChatSessionState).values(
        session_id=session_id, awaiting_save=value,
    ).on_conflict_do_update(
        index_elements=["session_id"],
        set_={"awaiting_save": value, "updated_at": func.now()},
    )
    await db.execute(stmt)
```

- [ ] **Step 5: Inside `_save_last_counterfactual`**, after a successful save, set `awaiting_save = False`.

- [ ] **Step 6: Run all asset_allocation tests**

```bash
pytest app/services/ai_bridge/asset_allocation/ -v
```

- [ ] **Step 7: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/chat.py app/services/ai_bridge/asset_allocation/tests/test_save_gate.py
git commit -m "feat(aa): explicit awaiting_save gate replaces history-inference"
```

### Task 3.5: Strip the now-redundant prose from `_DETECT_SYSTEM`

- [ ] **Step 1: Modify `chat.py:_DETECT_SYSTEM`** — delete the lines (around chat.py:154-159):

> "Only emit this mode when the IMMEDIATELY PRECEDING turn was a counterfactual_explore; if there's no recent counterfactual in the conversation history, this is misclassified — prefer narrate or redirect."

The classifier may still emit `save_last_counterfactual`; the *handler* now gates it. The prose is no longer load-bearing — keep `_DETECT_SYSTEM` short.

- [ ] **Step 2: Run all tests; commit if green.**

---

# PR 4: `compliance_check` + `prompt_versions` audit trail

**Goal:** Per-turn audit row for each guardrail rule that was evaluated against the formatter's output. SQL-queryable. Audit trail of *which* prompt version was active when each rule fired.

**Why last (sequencing, not technical dependency):** PR 4 is independently shippable in isolation — the compliance rules predicate on `output_text + facts_pack`, neither of which depends on PR 1, 2, or 3. The recommended order puts PR 4 last because: (a) PR 1 fixes an open correctness bug and ships first; (b) PR 2 stabilizes classifier mechanics so future rule predicates have a single integration point; (c) PR 3 introduces the `awaiting_save` primitive that some future compliance rules may want to inspect. None of this is mechanical dependency — it's organizational ordering for a cleaner end state.

**Files:**
- Create: `app/models/compliance.py`
- Create: `app/services/ai_bridge/compliance/__init__.py`
- Create: `app/services/ai_bridge/compliance/rules.py`
- Create: `alembic/versions/2026_05_04_b_compliance_audit.py`
- Modify: `app/models/__init__.py`
- Modify: `app/services/ai_bridge/answer_formatter/formatter.py:170-229` (extend `format_with_telemetry`)
- Test: `app/services/ai_bridge/answer_formatter/tests/test_compliance_audit.py` (new)

### Task 4.1: Add the models

- [ ] **Step 1: Create `app/models/compliance.py`**

```python
"""SQLAlchemy ORM models — prompt_versions, compliance_check.

prompt_versions: append-only registry of active system/body prompts. Each
distinct prompt blob registered at app boot gets one row keyed by sha256.

compliance_check: one row per (chat_ai_module_run_id, rule_id) — records
whether a SEBI-relevant rule was evaluated and whether it fired. SQL-queryable
audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    """e.g. 'aa_detect_system', 'formatter_house_style', 'aa_formatter_body'."""

    body: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComplianceCheck(Base):
    __tablename__ = "compliance_check"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chat_ai_module_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_ai_module_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    """e.g. 'no_scheme_name', 'fenced_numbers', 'no_fabricated_rupee'."""

    fired: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """True iff the rule's predicate matched (i.e. a violation was detected)."""

    output_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    """sha256 of the formatter output evaluated. Lets you reconstruct which
    output was being checked without storing the full text in this row."""

    prompt_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The active prompt version at the time of evaluation."""

    extra: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    """Rule-specific evidence (e.g. matched substring for no_scheme_name)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Register in `app/models/__init__.py`**

```python
from app.models.compliance import ComplianceCheck, PromptVersion  # noqa: F401
```

- [ ] **Step 3: Commit**

```bash
git add app/models/compliance.py app/models/__init__.py
git commit -m "feat(model): PromptVersion and ComplianceCheck"
```

### Task 4.2: Migration

- [ ] **Step 1:** `alembic revision -m "add_compliance_audit"`. Replace body with:

```python
def upgrade() -> None:
    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sha256", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "compliance_check",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chat_ai_module_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_ai_module_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("fired", sa.Boolean(), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column(
            "prompt_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prompt_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("extra", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_compliance_check_run_rule",
        "compliance_check",
        ["chat_ai_module_run_id", "rule_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_check_run_rule", table_name="compliance_check")
    op.drop_table("compliance_check")
    op.drop_table("prompt_versions")
```

- [ ] **Step 2:** `alembic upgrade head`. Commit.

### Task 4.3: Initial rule predicates

- [ ] **Step 1: Create `app/services/ai_bridge/compliance/rules.py`**

```python
"""SEBI-relevant compliance rules. Each rule is a pure function that takes the
formatter output + the FactsPack used to render it, and returns whether it
fired (True = violation detected) and an optional `extra` dict of evidence."""

from __future__ import annotations

import re
from typing import Any, Callable, NamedTuple

# A rule predicate returns (fired_bool, extra_dict_or_None).
RulePredicate = Callable[[str, dict[str, Any]], tuple[bool, dict[str, Any] | None]]


class Rule(NamedTuple):
    id: str
    predicate: RulePredicate


# ---- Rule 1: no specific mutual-fund scheme names. ----
# Today's defense: enforced by prompt prose. PR 4 makes it auditable.
# Kept conservative — false positives are acceptable in audit (we'd just see
# more rows; we never block output on these). Real-time blocking is out of scope.
_SCHEME_KEYWORDS = re.compile(
    r"\b(?:HDFC|ICICI|SBI|Axis|Kotak|UTI|Nippon|Aditya Birla|Mirae|Parag Parikh)"
    r"\s+(?:Bluechip|Smallcap|Midcap|Flexi[- ]?Cap|Equity|Hybrid|Liquid|Debt|"
    r"Index|ETF|Fund)\b",
    re.IGNORECASE,
)


def rule_no_scheme_name(output: str, facts_pack: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    m = _SCHEME_KEYWORDS.search(output or "")
    if m is None:
        return False, None
    return True, {"matched": m.group(0)}


# ---- Rule 2: no rupee figure absent from FactsPack. ----
# Detect "₹X.YZ crore" / "₹X.YZ lakh" / "Rs X" not present as a *_indian string
# in facts_pack. False positives possible; tune as data accumulates.
_RUPEE_PATTERN = re.compile(r"₹\s*[\d,]+(?:\.\d+)?\s*(?:crore|lakh|cr|lac)?", re.IGNORECASE)


def rule_no_fabricated_rupee(output: str, facts_pack: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    indian_strings: set[str] = set()
    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.endswith("_indian") and isinstance(v, str):
                    indian_strings.add(v.strip())
                collect(v)
        elif isinstance(node, list):
            for n in node:
                collect(n)
    collect(facts_pack)

    for match in _RUPEE_PATTERN.finditer(output or ""):
        candidate = match.group(0).strip()
        if not any(candidate in s or s in candidate for s in indian_strings):
            return True, {"unmatched_value": candidate}
    return False, None


# ---- Rule 3: no chart ASCII art in output. ----
# Already a house-style rule — make it auditable.
_ASCII_CHART = re.compile(r"[█▓▒░]{3,}")


def rule_no_ascii_chart(output: str, facts_pack: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    m = _ASCII_CHART.search(output or "")
    if m is None:
        return False, None
    return True, {"matched": m.group(0)}


# ---- Registry ----
ACTIVE_RULES: list[Rule] = [
    Rule(id="no_scheme_name", predicate=rule_no_scheme_name),
    Rule(id="no_fabricated_rupee", predicate=rule_no_fabricated_rupee),
    Rule(id="no_ascii_chart", predicate=rule_no_ascii_chart),
]
```

- [ ] **Step 2: Create `app/services/ai_bridge/compliance/__init__.py`**

```python
"""Compliance rule registry + audit-row writer."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceCheck
from app.services.ai_bridge.compliance.rules import ACTIVE_RULES

logger = logging.getLogger(__name__)


async def evaluate_and_record(
    *,
    db: AsyncSession | None,
    chat_ai_module_run_id: uuid.UUID,
    output_text: str,
    facts_pack: dict[str, Any],
    prompt_version_id: uuid.UUID | None = None,
) -> None:
    """Run every ACTIVE_RULES predicate against `output_text`; write one row
    per rule into compliance_check. Failures are logged, never raised — audit
    must not break a chat reply."""
    if db is None:
        return

    output_hash = hashlib.sha256((output_text or "").encode("utf-8")).hexdigest()

    for rule in ACTIVE_RULES:
        try:
            fired, extra = rule.predicate(output_text, facts_pack)
        except Exception as exc:
            logger.warning("compliance_rule_failed rule=%s err=%s", rule.id, exc)
            continue
        db.add(ComplianceCheck(
            chat_ai_module_run_id=chat_ai_module_run_id,
            rule_id=rule.id,
            fired=fired,
            output_hash=output_hash,
            prompt_version_id=prompt_version_id,
            extra=extra,
        ))
    # No flush here — caller's session controls commit timing.
```

- [ ] **Step 3: Commit**

```bash
git add app/services/ai_bridge/compliance/
git commit -m "feat(compliance): rule registry + audit-row writer"
```

### Task 4.4: Wire compliance writes into `format_with_telemetry`

> **Step ordering note:** Step 1 below requires `record_ai_module_run` to return the inserted row's `id`. It does not today. So Step 1 IS that telemetry change; Step 2 wires the compliance call.

- [ ] **Step 1: Update `record_ai_module_run`** in `app/services/ai_module_telemetry.py` to return the inserted row's `id`. The function today returns `None`; change the return type to `uuid.UUID | None` (None when `db is None`). Concretely, after the existing `db.add(row)` and `await db.flush()`, return `row.id`.

- [ ] **Step 2: Modify `formatter.py:170-229`** — after the `record_ai_module_run` call, capture the returned id and call `evaluate_and_record`:

```python
# In format_with_telemetry, replace the existing record_ai_module_run call:
    run_id = await record_ai_module_run(
        ctx.db,
        user_id=ctx.effective_user_id,
        session_id=ctx.session_id,
        module=module_name,
        reason=f"formatter:{action_mode}",
        duration_ms=latency_ms,
        formatter_invoked=True,
        formatter_succeeded=formatter_succeeded,
        formatter_latency_ms=latency_ms,
        formatter_error_class=formatter_error_class,
        action_mode=action_mode,
        emit_standard_log=False,
    )

    if ctx.db is not None and run_id is not None and formatter_succeeded:
        from app.services.ai_bridge.compliance import evaluate_and_record
        try:
            await evaluate_and_record(
                db=ctx.db,
                chat_ai_module_run_id=run_id,
                output_text=text,
                facts_pack=facts_pack,
                prompt_version_id=None,  # populated by Task 4.5
            )
        except Exception as exc:
            logger.warning("compliance_eval_failed module=%s err=%s", module_name, exc)
```

- [ ] **Step 3: Add a test** in `app/services/ai_bridge/answer_formatter/tests/test_compliance_audit.py`:

```python
@pytest.mark.asyncio
async def test_format_with_telemetry_writes_compliance_rows(
    user_with_alloc: User, db: AsyncSession,
) -> None:
    from app.models.compliance import ComplianceCheck
    from app.services.ai_bridge.answer_formatter.formatter import format_with_telemetry
    from app.services.chat_core.turn_context import TurnContext
    from sqlalchemy import select

    ctx = TurnContext(
        user_ctx=user_with_alloc, user_question="x",
        conversation_history=[], client_context=None,
        session_id=uuid.uuid4(), db=db,
        effective_user_id=user_with_alloc.id,
        last_agent_runs={}, active_intent="asset_allocation",
        chat_overrides=None, awaiting_save=False,
    )
    facts_pack = {"funding_gap_indian": "₹1 lakh"}

    # Patch the LLM to return a known string.
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        AsyncMock(return_value="Your funding gap is ₹1 lakh."),
    ):
        text = await format_with_telemetry(
            ctx=ctx, facts_pack=facts_pack,
            body_prompt="x", module_name="asset_allocation",
            action_mode="narrate", profile={},
            build_fallback=lambda: "fallback",
        )

    rows = (await db.execute(select(ComplianceCheck))).scalars().all()
    rule_ids = {r.rule_id for r in rows}
    # All ACTIVE_RULES were evaluated for this turn:
    assert rule_ids == {"no_scheme_name", "no_fabricated_rupee", "no_ascii_chart"}
    # No violations expected on the safe text:
    assert all(not r.fired for r in rows)
```

- [ ] **Step 4: Run; commit when green.**

### Task 4.5: Register active prompts at boot, populate prompt_version_id

- [ ] **Step 1: Add a startup hook** in `app/main.py` (or `app/services/ai_bridge/__init__.py`) that hashes each active prompt and upserts a `prompt_versions` row.

```python
# app/services/ai_bridge/compliance/__init__.py — add:
import hashlib
from app.models.compliance import PromptVersion

_PROMPT_VERSION_CACHE: dict[str, uuid.UUID] = {}


async def register_prompt(db: AsyncSession, name: str, body: str) -> uuid.UUID:
    """Idempotent register: returns the prompt_versions.id for this body."""
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if sha in _PROMPT_VERSION_CACHE:
        return _PROMPT_VERSION_CACHE[sha]
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy import select
    stmt = insert(PromptVersion).values(
        sha256=sha, name=name, body=body,
    ).on_conflict_do_nothing(index_elements=["sha256"])
    await db.execute(stmt)
    res = await db.execute(select(PromptVersion.id).where(PromptVersion.sha256 == sha))
    pv_id = res.scalar_one()
    _PROMPT_VERSION_CACHE[sha] = pv_id
    return pv_id
```

- [ ] **Step 2: Update `format_with_telemetry`** to register the body_prompt + house-style and pass the resulting id into `evaluate_and_record`.

- [ ] **Step 3: Test that compliance_check rows have non-null prompt_version_id; commit.**

---

## Out of scope (explicit, with rationale)

These are *not* in this plan. They're listed so they don't get pulled in by scope creep.

| Item | Why deferred |
|---|---|
| **SessionSummarizer** for cross-session multi-year memory | Honestly conceded as research-grade in the verdict. First three iterations will be wrong. Worth its own design doc; pre-planning during the migration adds risk without value. |
| **`goal_planning` LangGraph carve-out** | No `goal_planning` chat handler exists today — only a redirect in `brain.py`. Carve-out criteria documented below for trigger-time review. |
| **22-tool tripwire CI** | Operational discipline. A line in the quarterly architecture review checklist does the same job for less code. |
| **Migration off `langchain-anthropic`** | Forbidden by `Prozpr_Backend/CLAUDE.md` convention. The Hybrid spirit (native tool-call loop, 2 LLM calls/turn) is achieved via `ChatAnthropic.bind_tools()`. |
| **Unifying `ChatAction` + `RebalanceAction` schemas** | Their mode spaces differ. Forcing one schema would lose typed precision. PR 2 unifies *mechanics*, not *schema*. |
| **Real-time blocking on compliance violations** | `compliance_check` is an audit log. Blocking would require a retry-with-correction loop and is a much bigger product change. |

## Forward-looking criteria

### LangGraph carve-out — when to promote a flow

Per the verdict, a flow becomes a LangGraph subgraph candidate when **all three** are true:

1. The flow has 4+ branching conditions.
2. Cross-turn resumability requirements that the (still-unwritten) SessionSummarizer cannot express.
3. The flow is one of: `goal_planning`, `tax_planning`, `rebalancing-with-TLH`.

Not 2 of 3. All three. This prevents the slope from "Hybrid + 1 subgraph" to "Hybrid + 5 subgraphs and actually-LangGraph."

When the criteria fire, write a separate spec — do not extend this plan.

## Operational checklist (the 22-tool tripwire)

- Add to the quarterly architecture review agenda:

> **Hybrid migration check**
> 1. Count distinct intent handlers registered in `chat_dispatcher._HANDLERS` (today: 1 — `asset_allocation`. Add `rebalancing` when its handler registers; add others as they land).
> 2. Count tool-shaped engine wrappers exposed to the LLM via `bind_tools()` (today: 0 — engines are still called directly by per-module handlers).
> 3. If (1) + (2) ≥ 22, open the carve-out review. Apply the all-three criteria above.

This is a checklist, not a CI gate. CI for a 4-year-out trigger is speculative tooling.

## Verification gates between PRs

| After | Must pass |
|---|---|
| PR 1 | `grep -rn "_chat_.*_override" Prozpr_Backend/app/ AI_Agents/src/` returns empty. `pytest app/services/ai_bridge/asset_allocation/` green. |
| PR 2 | `grep -rn "with_structured_output(ChatAction\|with_structured_output(RebalanceAction" Prozpr_Backend/app/services/` returns ≤ 1 match. `pytest app/services/ai_bridge/` green. |
| PR 3 | `alembic upgrade head` clean. `pytest app/services/chat_core/ app/services/ai_bridge/asset_allocation/` green. Manual test: enter a counterfactual, switch session, return — `awaiting_save` is False (cross-session reset). |
| PR 4 | After one chat turn against a real engine, `SELECT COUNT(*) FROM compliance_check WHERE created_at > NOW() - INTERVAL '5 minutes';` returns ≥ 3 (one per ACTIVE_RULES). |

**Gating rule:** No new engine-wrapping tools land between PR 1 and PR 4 completing. The `chat_dispatcher` registry is frozen for the duration.

---

## Self-review notes

This plan is reviewed against three explicit criteria before handoff:

1. **Every line in every PR traces to an admitted weakness from the verdict.** PR 1 = User monkey-patch. PR 2 = `_detect_action` duplication. PR 3 = implicit awaiting-state. PR 4 = static prompts in code + post-hoc validators with no audit trail.
2. **Every PR has a test that fails before the change and passes after.** Each task block opens with a failing test, then implements minimum to pass.
3. **No PR depends on infrastructure introduced by a *later* PR.** PR 1 ships standalone. PR 2 ships standalone. PR 3 ships standalone. PR 4 reads `prompt_versions` (created in PR 4 itself) and `compliance_check` (also PR 4) — no forward dependency.

If a reviewer finds a counterexample to (1), (2), or (3), the plan needs an edit. The default answer is to extend a PR, not to add a fifth.

## Self-review findings (already fixed inline)

The author found and fixed five issues during the self-review pass:

1. **PR 1 circular-import bug.** Original draft had `input_builder.py` import from `chat.py`, closing a `chat.py → service.py → input_builder.py → chat.py` cycle at module load. Fix: introduced a leaf module `app/services/ai_bridge/asset_allocation/overrides.py` that both files import from. (See Task 1.3.)
2. **PR 1 signature scope creep.** Original draft proposed changing `compute_allocation_result(user, ...)` to `(ctx, ...)`, which would break the standalone HTTP allocation endpoint. Fix: keep `compute_allocation_result` signature; thread `ctx` via a new `chat_ctx=` kwarg, with a synthetic empty-overrides ctx constructed for non-chat callers. (See Task 1.5 step 3.)
3. **PR 3 placeholder violation.** Test had `last_agent_runs={"asset_allocation": ...}` with literal `...`. Fix: replaced with a concrete `AgentRunRecord` stub. (See Task 3.4 step 1.)
4. **PR 4 step ordering.** Original Step 1 referenced a `run_id` that Step 2 introduced. Fix: swapped — telemetry change is Step 1, formatter wiring is Step 2.
5. **Misleading dependency framing.** Original "PR 4 depends on PR 2 and PR 3" was technically false. Fix: reframed as "organizational ordering, not mechanical dependency" in PR 4's intro.

