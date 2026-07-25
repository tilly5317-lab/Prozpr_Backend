# Unified Chat Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the asset-allocation chat path into a single per-intent module that owns every chat-side decision (run engine / narrate / educate / counterfactual / clarify / recompute / redirect). Brain becomes a thin dispatcher; classifier loses load-bearing routing flags. Sets the pattern for rebalancing and other future modules.

**Architecture:** New `chat_dispatcher` (registry, replaces `followup_dispatcher`) + new `asset_allocation_chat.py` (unified handler with 7 modes + first-turn engine path) + brain refactor to one-line dispatch + classifier cleanup (drop `wants_fresh_recomputation`) + delete the old `asset_allocation_followup` and `_counterfactual` files. Rip-and-replace because nothing here is in prod.

**Tech Stack:** FastAPI, SQLAlchemy async (PostgreSQL JSONB on prod, JSON on dev SQLite), Pydantic v2, LangChain + Anthropic Claude Haiku 4.5, pytest + unittest.

**Spec:** `docs/superpowers/specs/2026-04-28-unified-chat-modules-design.md`

**Run tests with:** `python3 -m pytest <path> -v` (system `python3` is what uvicorn uses; `python` is not on PATH).

**Commit format:** HEREDOC, ending with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

**Memory constraint:** Don't commit anything under `CLAUDE.md` or `docs/superpowers/`.

---

## File structure summary

| File | Action | Notes |
|---|---|---|
| `app/services/ai_bridge/goal_allocation_input_builder.py` | Modify | Extend transient-attr override to 6 keys |
| `app/services/ai_bridge/chat_dispatcher.py` | **New** | Registry with `dispatch_chat(intent, ctx) → ChatHandlerResult` |
| `app/services/ai_bridge/asset_allocation_chat.py` | **New** | Unified handler; all 7 modes + first-turn engine path |
| `app/services/chat_core/brain.py` | Modify | Strip `_answer_portfolio_style`, dispatch via `chat_dispatcher` |
| `AI_Agents/src/intent_classifier/models.py` | Modify | Drop `wants_fresh_recomputation` field |
| `AI_Agents/src/intent_classifier/classifier.py` | Modify | Drop forwarding of dropped field |
| `AI_Agents/src/intent_classifier/prompts.py` | Modify | Drop `## Recomputation Detection` section |
| `app/services/ai_bridge/asset_allocation_followup.py` | **Delete** | Superseded by `asset_allocation_chat.py` |
| `app/services/ai_bridge/asset_allocation_followup_counterfactual.py` | **Delete** | Same |
| `app/services/ai_bridge/followup_dispatcher.py` | **Delete** | Superseded by `chat_dispatcher.py` |
| `app/services/ai_bridge/tests/test_asset_allocation_chat.py` | **New** | All-mode test suite |
| `app/services/ai_bridge/tests/test_chat_dispatcher.py` | **New** | Replaces old followup_dispatcher test |
| `app/services/ai_bridge/tests/test_asset_allocation_followup.py` | **Delete** | Superseded |
| `app/services/ai_bridge/tests/test_followup_dispatcher.py` | **Delete** | Superseded |
| `AI_Agents/tests/test_intent_classifier.py` | Modify | Remove `WantsFreshRecomputationFieldTests` |

`app/services/ai_bridge/asset_allocation_service.py` stays as-is (pure engine + presentation primitives).

---

## Task 1: Extend input builder for 6 override keys

**Files:**
- Modify: `app/services/ai_bridge/goal_allocation_input_builder.py:188-217`
- Modify: `app/services/ai_bridge/tests/test_goal_allocation_input_builder.py` (append override tests)

The current builder honors only `_chat_risk_score_override`. Extend to honor 6 keys via transient `User` attributes. The keys + their builder-locals to override:

| Override attribute | Builder local | Type |
|---|---|---|
| `_chat_risk_score_override` | `effective_risk_score` | float (clamped to [1, 10]) |
| `_chat_total_corpus_override` | `total_corpus` | float (≥0) |
| `_chat_annual_income_override` | `annual_income` | float (≥0) |
| `_chat_monthly_expense_override` | `monthly_household_expense` | float (≥0) |
| `_chat_emergency_fund_needed_override` | passed to `AllocationInput(emergency_fund_needed=...)` | bool |
| `_chat_tax_regime_override` | passed to `AllocationInput(tax_regime=...)` | "old" \| "new" |

- [ ] **Step 1: Read the current override block**

```bash
sed -n '188,220p' app/services/ai_bridge/goal_allocation_input_builder.py
```

You should see lines 188–194 (goals + the existing `_chat_risk_score_override` block) followed by the `AllocationInput(...)` constructor on 196–217.

- [ ] **Step 2: Write failing tests**

Find the existing test file:
```bash
ls app/services/ai_bridge/tests/test_goal_allocation_input_builder.py
```

Append a new test class to it (preserve any existing imports — match the style used by the existing tests):

```python
class ChatOverrideTests(unittest.TestCase):
    """Transient _chat_*_override attributes on User flow into AllocationInput."""

    def _build_minimal_user(self):
        """Build a User-like stub the builder accepts. Adapt to the existing
        helper if one already exists in this test file."""
        from datetime import date
        from unittest.mock import MagicMock

        user = MagicMock()
        user.date_of_birth = date(1986, 1, 1)
        user.first_name = "Tilly"
        user.investment_profile = MagicMock(
            annual_income=1_000_000.0,
            net_financial_assets=8_000_000.0,
            monthly_outgoings=50_000.0,
            primary_income_from_portfolio=False,
            intergenerational_transfer=False,
            emergency_fund=200_000.0,
        )
        user.risk_profile = MagicMock(effective_risk_score=5.4)
        user.effective_risk_assessment = None
        user.tax_profile = MagicMock(effective_tax_rate=30.0, tax_regime="new")
        user.financial_goals = []
        user.investment_constraints = MagicMock()
        return user

    def test_risk_score_override_already_works(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        user._chat_risk_score_override = 8.0

        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.effective_risk_score, 8.0)

    def test_total_corpus_override(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        user._chat_total_corpus_override = 12_000_000.0

        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.total_corpus, 12_000_000.0)

    def test_annual_income_override(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        user._chat_annual_income_override = 3_000_000.0

        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.annual_income, 3_000_000.0)

    def test_monthly_expense_override(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        user._chat_monthly_expense_override = 30_000.0

        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.monthly_household_expense, 30_000.0)

    def test_emergency_fund_needed_override(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        user._chat_emergency_fund_needed_override = True

        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertTrue(alloc_input.emergency_fund_needed)

    def test_tax_regime_override(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        user._chat_tax_regime_override = "old"

        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.tax_regime, "old")

    def test_no_overrides_returns_baseline(self):
        from app.services.ai_bridge.goal_allocation_input_builder import (
            build_goal_allocation_input_for_user,
        )
        user = self._build_minimal_user()
        # No _chat_*_override attributes set — baseline should be used
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.effective_risk_score, 5.4)
```

If the existing test file's user-stub helper differs significantly, adapt `_build_minimal_user` to use it.

- [ ] **Step 3: Run tests — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_goal_allocation_input_builder.py::ChatOverrideTests -v
```
Expected: 6 failures (only `test_risk_score_override_already_works` and `test_no_overrides_returns_baseline` may pass since the existing risk override is in place).

- [ ] **Step 4: Implement — extend the override block**

Edit `app/services/ai_bridge/goal_allocation_input_builder.py`. Replace the existing 4-line override block (around line 190–194) with this expanded block:

```python
    # Counterfactual override path: chat-only, transient attributes set by
    # asset_allocation_chat. Each one overrides a specific AllocationInput field.
    _risk_override = getattr(user, "_chat_risk_score_override", None)
    if _risk_override is not None:
        effective_risk_score = _clamp_score(float(_risk_override))

    _corpus_override = getattr(user, "_chat_total_corpus_override", None)
    if _corpus_override is not None:
        total_corpus = float(_corpus_override)

    _income_override = getattr(user, "_chat_annual_income_override", None)
    if _income_override is not None:
        annual_income = float(_income_override)

    _expense_override = getattr(user, "_chat_monthly_expense_override", None)
    if _expense_override is not None:
        monthly_household_expense = float(_expense_override)

    _emergency_override = getattr(user, "_chat_emergency_fund_needed_override", None)
    _tax_regime_override = getattr(user, "_chat_tax_regime_override", None)
```

Then update the `AllocationInput(...)` constructor (lines 196–217) to use the new override locals for the two fields that aren't already pulled from a local variable:

Find the lines:
```python
        tax_regime="new",
        ...
        emergency_fund_needed=False,
```

Replace them with:
```python
        tax_regime=_tax_regime_override if _tax_regime_override in ("old", "new") else "new",
        ...
        emergency_fund_needed=bool(_emergency_override) if _emergency_override is not None else False,
```

(Keep all other fields in the constructor unchanged.)

- [ ] **Step 5: Run tests — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_goal_allocation_input_builder.py::ChatOverrideTests -v
```
Expected: 7 passed.

- [ ] **Step 6: Run all earlier task tests — confirm no regression**

```bash
python3 -m pytest app/services/tests/ app/services/ai_bridge/tests/ app/services/chat_core/tests/ AI_Agents/tests/test_intent_classifier.py -v 2>&1 | tail -10
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add app/services/ai_bridge/goal_allocation_input_builder.py app/services/ai_bridge/tests/test_goal_allocation_input_builder.py
git status   # confirm only those 2 files
git commit -m "$(cat <<'EOF'
feat: extend input builder to honor 6 chat-driven override attributes

Adds transient _chat_*_override attribute support for total_corpus,
annual_income, monthly_household_expense, emergency_fund_needed, and
tax_regime, alongside the existing _chat_risk_score_override. Used by
the upcoming asset_allocation_chat module's counterfactual and
recompute_with_overrides modes. Pure additive change; nothing else
is affected if the attributes aren't set.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: New `chat_dispatcher.py`

**Files:**
- Create: `app/services/ai_bridge/chat_dispatcher.py`
- Create: `app/services/ai_bridge/tests/test_chat_dispatcher.py`

This dispatcher coexists with `followup_dispatcher.py` for now. The old one stays in use until Task 4's brain switch. Same registry pattern, but the handler signature is `(turn_context) → ChatHandlerResult` instead of `(agent_run, turn_context) → str`. Handlers pull from `turn_context.last_agent_runs` themselves (necessary because first-turn handlers don't have an agent run yet).

- [ ] **Step 1: Write failing tests**

Create `app/services/ai_bridge/tests/test_chat_dispatcher.py`:

```python
"""chat_dispatcher: registry + dispatch behavior (new signature)."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import MagicMock

from app.services.ai_bridge import chat_dispatcher as cd
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult


class ChatDispatcherTests(unittest.TestCase):

    def setUp(self):
        cd._HANDLERS.clear()

    def test_register_and_dispatch_calls_handler(self):
        called = {}

        @cd.register("portfolio_optimisation")
        async def fake_handler(ctx):
            called["ctx"] = ctx
            return ChatHandlerResult(text="hello")

        ctx = MagicMock()
        result = asyncio.run(cd.dispatch_chat("portfolio_optimisation", ctx))
        self.assertIsInstance(result, ChatHandlerResult)
        self.assertEqual(result.text, "hello")
        self.assertIs(called["ctx"], ctx)

    def test_unregistered_intent_raises(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(cd.dispatch_chat("no_such_intent", MagicMock()))

    def test_register_multiple_intents_for_one_handler(self):
        @cd.register("portfolio_optimisation")
        @cd.register("goal_planning")
        async def shared(ctx):
            return ChatHandlerResult(text="shared")

        for intent in ("portfolio_optimisation", "goal_planning"):
            self.assertEqual(
                asyncio.run(cd.dispatch_chat(intent, MagicMock())).text,
                "shared",
            )

    def test_chat_handler_result_carries_optional_ids(self):
        snap = uuid.uuid4()
        rec = uuid.uuid4()
        result = ChatHandlerResult(text="ok", snapshot_id=snap, rebalancing_recommendation_id=rec)
        self.assertEqual(result.snapshot_id, snap)
        self.assertEqual(result.rebalancing_recommendation_id, rec)
        # Defaults are None
        self.assertIsNone(ChatHandlerResult(text="x").snapshot_id)
        self.assertIsNone(ChatHandlerResult(text="x").rebalancing_recommendation_id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_chat_dispatcher.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `chat_dispatcher.py`**

Create `app/services/ai_bridge/chat_dispatcher.py`:

```python
"""Per-intent chat handler registry + dispatcher.

Each chat-facing intent has exactly one handler module that registers itself
via @register(intent) at import time. The handler receives a TurnContext and
returns a ChatHandlerResult (text + optional snapshot/recommendation IDs).

This replaces ``followup_dispatcher.py`` — the new signature passes only
the TurnContext (handlers pull last_agent_runs from there themselves), so
first-turn handlers (no AgentRun yet) and follow-up handlers share one
entry point.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import TurnContext


@dataclass(frozen=True)
class ChatHandlerResult:
    """Return shape for every chat handler. Forwarded to ChatBrainResult."""
    text: str
    snapshot_id: uuid.UUID | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None


Handler = Callable[["TurnContext"], Awaitable[ChatHandlerResult]]

_HANDLERS: dict[str, Handler] = {}


def register(intent: str) -> Callable[[Handler], Handler]:
    """Register a chat handler for the given intent. Stackable."""
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[intent] = fn
        return fn
    return decorator


async def dispatch_chat(
    intent: str, turn_context: "TurnContext",
) -> ChatHandlerResult:
    """Look up the handler for ``intent`` and invoke it."""
    handler = _HANDLERS.get(intent)
    if handler is None:
        raise RuntimeError(
            f"No chat handler registered for intent={intent!r}"
        )
    return await handler(turn_context)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_chat_dispatcher.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/chat_dispatcher.py app/services/ai_bridge/tests/test_chat_dispatcher.py
git status   # confirm only those 2 files
git commit -m "$(cat <<'EOF'
feat: chat_dispatcher with ChatHandlerResult — per-intent handler registry

New signature: handlers take TurnContext and return ChatHandlerResult
(text + optional snapshot/recommendation IDs). First-turn and follow-up
handlers share one entry point — handler pulls last_agent_runs from ctx
itself.

Coexists with the existing followup_dispatcher.py until the brain
switches over (next commit) and the old dispatcher is deleted.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: New `asset_allocation_chat.py` — unified handler with all 7 modes

**Files:**
- Create: `app/services/ai_bridge/asset_allocation_chat.py`
- Create: `app/services/ai_bridge/tests/test_asset_allocation_chat.py`

This is the largest task. The module owns the full chat lifecycle for `portfolio_optimisation`/`goal_planning` intents. It self-registers with `chat_dispatcher`. It does not yet replace the old followup module — Task 4 switches the brain over.

**Module structure:**
- `ChatAction` Pydantic model with 7-mode Literal + override/clarification/redirect fields
- `_DETECT_SYSTEM` system prompt explaining the modes + override allow-list
- `_NARRATE_SYSTEM`, `_EDUCATE_SYSTEM`, `_COUNTERFACTUAL_NARRATE_SYSTEM` prompts
- `handle()` — registered entry point. Dispatches first-turn vs follow-up vs detect_action paths
- `_first_turn_run_engine(ctx)` — runs engine, persists, returns `ChatHandlerResult`
- `_detect_action(ctx, last_alloc)` — one Haiku structured-output call → `ChatAction`
- Per-mode handlers: `_narrate`, `_educate`, `_counterfactual_explore`, `_clarify`, `_recompute_full`, `_recompute_with_overrides`, `_redirect`
- `_apply_overrides(user, overrides)` / `_clear_overrides(user)` — set/clear transient `_chat_*_override` attributes
- `_OVERRIDE_KEY_TO_USER_ATTR` — mapping from `ChatAction.overrides` keys to `User._chat_*_override` attribute names

- [ ] **Step 1: Write failing tests**

Create `app/services/ai_bridge/tests/test_asset_allocation_chat.py`:

```python
"""asset_allocation_chat: unified handler with all 7 modes."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge import asset_allocation_chat as mod
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="goal_based_allocation",
        intent_detected="portfolio_optimisation",
        input_payload={
            "effective_risk_score": 5.4, "age": 39, "annual_income": 1_000_000,
            "osi": 0.3, "savings_rate_adjustment": "none", "gap_exceeds_3": False,
            "total_corpus": 8_000_000, "monthly_household_expense": 50_000,
            "tax_regime": "new", "effective_tax_rate": 30.0, "goals": [],
        },
        output_payload={
            "allocation_result": {
                "grand_total": 8_000_000,
                "asset_class_breakdown": {
                    "actual": {
                        "equity_total_pct": 40.2,
                        "debt_total_pct": 51.0,
                        "others_total_pct": 8.8,
                    },
                },
            },
            "correlation_ids": {"snapshot_id": str(uuid.uuid4()),
                                "rebalancing_recommendation_id": str(uuid.uuid4())},
        },
        created_at=datetime.utcnow(),
    )


def _ctx(question: str, *, last_alloc: AgentRunRecord | None = None) -> TurnContext:
    last_runs = {"goal_based_allocation": last_alloc} if last_alloc else {}
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=date(1986, 1, 1), first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs=last_runs,
        active_intent="portfolio_optimisation",
    )


class FirstTurnTests(unittest.TestCase):
    """When no AgentRun exists for goal_based_allocation, run engine."""

    def test_first_turn_runs_engine_and_returns_ids(self):
        outcome = MagicMock()
        outcome.result = MagicMock()
        outcome.result.grand_total = 8_000_000
        outcome.result.client_summary = MagicMock(
            effective_risk_score=5.4, age=39, goals=[]
        )
        outcome.result.bucket_allocations = []
        outcome.result.asset_class_breakdown = None
        outcome.result.aggregated_subgroups = []
        outcome.result.future_investments_summary = []
        outcome.blocking_message = None
        outcome.allocation_snapshot_id = uuid.uuid4()
        outcome.rebalancing_recommendation_id = uuid.uuid4()

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertIsInstance(result, ChatHandlerResult)
        self.assertEqual(result.snapshot_id, outcome.allocation_snapshot_id)
        self.assertEqual(result.rebalancing_recommendation_id,
                         outcome.rebalancing_recommendation_id)
        self.assertIn("Here is", result.text) if result.text else None  # chat brief

    def test_first_turn_blocking_message_passes_through(self):
        outcome = MagicMock()
        outcome.result = None
        outcome.blocking_message = "I need your date of birth..."
        outcome.allocation_snapshot_id = None
        outcome.rebalancing_recommendation_id = None

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(result.text, "I need your date of birth...")
        self.assertIsNone(result.snapshot_id)


class NarrateModeTests(unittest.TestCase):

    def test_narrate_returns_text_no_engine(self):
        action = mod.ChatAction(mode="narrate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_narrate_with_llm",
                          new=AsyncMock(return_value="narration text")), \
             patch.object(mod, "compute_allocation_result",
                          new=AsyncMock()) as engine:
            result = asyncio.run(mod.handle(_ctx("is this too aggressive?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "narration text")
        engine.assert_not_called()


class EducateModeTests(unittest.TestCase):

    def test_educate_returns_text_no_engine(self):
        action = mod.ChatAction(mode="educate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_educate_with_llm",
                          new=AsyncMock(return_value="educational text")), \
             patch.object(mod, "compute_allocation_result",
                          new=AsyncMock()) as engine:
            result = asyncio.run(mod.handle(_ctx("what does multi-cap mean?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "educational text")
        engine.assert_not_called()


class CounterfactualExploreTests(unittest.TestCase):

    def test_counterfactual_explore_runs_engine_no_persist(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["db"] = kwargs.get("db")
            captured["risk_override_seen"] = getattr(user, "_chat_risk_score_override", None)
            outcome = MagicMock()
            outcome.result = MagicMock(grand_total=8_000_000)
            outcome.result.model_dump = MagicMock(return_value={"grand_total": 8_000_000})
            outcome.blocking_message = None
            return outcome

        action = mod.ChatAction(mode="counterfactual_explore",
                                 overrides={"effective_risk_score": 7.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute), \
             patch.object(mod, "_narrate_counterfactual",
                          new=AsyncMock(return_value="hypothetical text")):
            result = asyncio.run(mod.handle(_ctx("what if risk were 7?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "hypothetical text")
        self.assertFalse(captured["persist"])
        self.assertIsNone(captured["db"])
        self.assertEqual(captured["risk_override_seen"], 7.0)
        self.assertIsNone(result.snapshot_id)

    def test_counterfactual_with_invalid_override_falls_to_redirect(self):
        action = mod.ChatAction(mode="counterfactual_explore",
                                 overrides={"unknown_key": 1.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("what if?", last_alloc=_agent_run())))
        self.assertIn("Profile", result.text)


class ClarifyModeTests(unittest.TestCase):

    def test_clarify_returns_composed_question(self):
        action = mod.ChatAction(mode="clarify",
                                 clarification_question="What risk score?")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("I want more risk", last_alloc=_agent_run())))
        self.assertEqual(result.text, "What risk score?")
        self.assertIsNone(result.snapshot_id)

    def test_clarify_without_question_uses_fallback(self):
        action = mod.ChatAction(mode="clarify", clarification_question=None)
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("I want something", last_alloc=_agent_run())))
        self.assertTrue(result.text)  # non-empty fallback


class RecomputeFullTests(unittest.TestCase):

    def test_recompute_full_runs_engine_and_persists(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["db"] = kwargs.get("db")
            outcome = MagicMock()
            outcome.result = MagicMock()
            outcome.result.grand_total = 8_000_000
            outcome.result.client_summary = MagicMock(
                effective_risk_score=5.4, age=39, goals=[]
            )
            outcome.result.bucket_allocations = []
            outcome.result.asset_class_breakdown = None
            outcome.result.aggregated_subgroups = []
            outcome.result.future_investments_summary = []
            outcome.blocking_message = None
            outcome.allocation_snapshot_id = uuid.uuid4()
            outcome.rebalancing_recommendation_id = uuid.uuid4()
            return outcome

        action = mod.ChatAction(mode="recompute_full")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute):
            ctx = _ctx("redo my plan", last_alloc=_agent_run())
            result = asyncio.run(mod.handle(ctx))

        self.assertTrue(captured["persist"])
        self.assertIsNotNone(captured["db"])
        self.assertIsNotNone(result.snapshot_id)
        self.assertIsNotNone(result.rebalancing_recommendation_id)


class RecomputeWithOverridesTests(unittest.TestCase):

    def test_recompute_with_overrides_persists_with_override_applied(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["risk_override_seen"] = getattr(user, "_chat_risk_score_override", None)
            outcome = MagicMock()
            outcome.result = MagicMock()
            outcome.result.grand_total = 8_000_000
            outcome.result.client_summary = MagicMock(
                effective_risk_score=7.0, age=39, goals=[]
            )
            outcome.result.bucket_allocations = []
            outcome.result.asset_class_breakdown = None
            outcome.result.aggregated_subgroups = []
            outcome.result.future_investments_summary = []
            outcome.blocking_message = None
            outcome.allocation_snapshot_id = uuid.uuid4()
            outcome.rebalancing_recommendation_id = uuid.uuid4()
            return outcome

        action = mod.ChatAction(mode="recompute_with_overrides",
                                 overrides={"effective_risk_score": 7.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute):
            result = asyncio.run(mod.handle(_ctx("lock in risk 7", last_alloc=_agent_run())))

        self.assertTrue(captured["persist"])
        self.assertEqual(captured["risk_override_seen"], 7.0)
        self.assertIsNotNone(result.snapshot_id)


class RedirectModeTests(unittest.TestCase):

    def test_redirect_returns_template_with_reason(self):
        action = mod.ChatAction(mode="redirect", redirect_reason="change holdings")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("swap arbitrage", last_alloc=_agent_run())))
        self.assertIn("Profile", result.text)
        self.assertIn("change holdings", result.text)


class DetectActionFailureTests(unittest.TestCase):

    def test_detect_action_failure_falls_back_to_narrate(self):
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(side_effect=RuntimeError("LLM down"))), \
             patch.object(mod, "_narrate_with_llm",
                          new=AsyncMock(return_value="best-effort narration")):
            result = asyncio.run(mod.handle(_ctx("what?", last_alloc=_agent_run())))
        self.assertEqual(result.text, "best-effort narration")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_chat.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `asset_allocation_chat.py`**

Create `app/services/ai_bridge/asset_allocation_chat.py`:

```python
"""Unified chat handler for portfolio_optimisation / goal_planning intents.

Single entry point for the entire chat lifecycle of allocation conversations:
- First turn (no AgentRun for goal_based_allocation in session) → run engine,
  persist, return chat brief
- Subsequent turns → call _detect_action LLM to pick one of 7 modes
  (narrate / educate / counterfactual_explore / clarify / recompute_full /
   recompute_with_overrides / redirect), then dispatch.

Replaces the asset_allocation_followup + asset_allocation_followup_counterfactual
split. The engine wrapper compute_allocation_result stays in
asset_allocation_service.py and is consumed by both this module and the
standalone HTTP endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_bridge.asset_allocation_service import (
    compute_allocation_result,
    format_allocation_chat_brief,
)
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.ailax_trace import trace_line
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action schema (structured output of _detect_action)
# ---------------------------------------------------------------------------

class ChatAction(BaseModel):
    mode: Literal[
        "narrate",
        "educate",
        "counterfactual_explore",
        "clarify",
        "recompute_full",
        "recompute_with_overrides",
        "redirect",
    ]
    overrides: Optional[dict[str, Any]] = Field(
        default=None,
        description="For counterfactual_explore + recompute_with_overrides. "
                    "Allowed keys: effective_risk_score, total_corpus, "
                    "annual_income, monthly_household_expense, "
                    "emergency_fund_needed, tax_regime.",
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="When mode='clarify', the question to ask the customer.",
    )
    redirect_reason: Optional[str] = Field(
        default=None,
        description="When mode='redirect', a short description of what the user wants.",
    )


# ---------------------------------------------------------------------------
# Override allow-list
# ---------------------------------------------------------------------------

# Maps ChatAction.overrides keys → transient User attribute names that
# goal_allocation_input_builder reads.
_OVERRIDE_KEY_TO_USER_ATTR: dict[str, str] = {
    "effective_risk_score":      "_chat_risk_score_override",
    "total_corpus":              "_chat_total_corpus_override",
    "annual_income":             "_chat_annual_income_override",
    "monthly_household_expense": "_chat_monthly_expense_override",
    "emergency_fund_needed":     "_chat_emergency_fund_needed_override",
    "tax_regime":                "_chat_tax_regime_override",
}

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** section and update the relevant "
    "inputs — I'll regenerate your plan automatically. If you'd like, just "
    "describe what you want differently and I can run a hypothetical."
)

_INVALID_OVERRIDE_TEMPLATE = (
    "I can only run 'what if' on a small set of inputs from chat right now "
    "(risk score, total corpus, income, expenses, emergency fund, tax regime). "
    "For other changes, head to your **Profile** section and I'll regenerate "
    "your plan automatically."
)

_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific risk score (1–10), "
    "fund name, or amount you'd like to consider?"
)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_DETECT_SYSTEM = """You decide how to handle a chat turn about a customer's
goal-based asset allocation. Pick exactly one of seven modes:

- "narrate" — explanation, critique, or "why" questions about the existing
  plan ("is this too aggressive?", "why so much arbitrage?").
- "educate" — educational questions grounded in the snapshot ("what does
  multi-cap mean?", "how does the tax treatment work?", "what is an
  arbitrage fund?"). Distinguishable from narrate: focus is teaching a
  concept, often using the user's specific holdings as examples.
- "counterfactual_explore" — hypothetical "what if" questions where the
  user wants to see the impact of a single input change without committing
  ("what if my risk were 7?", "what if I had ₹1 crore?"). Must specify
  `overrides` with one or more allowed keys.
- "clarify" — the customer signals a direction but doesn't provide an
  actionable value ("I can take more risk", "I want to be more conservative",
  "less debt please"). Compose a concise clarification question in
  `clarification_question` that asks for the missing value. Reference current
  values from the snapshot when possible (e.g., "Your current risk score is
  5.5 — would 7 feel right?").
- "recompute_full" — the customer explicitly asks to re-run the full plan
  with their current saved inputs ("redo my plan", "rerun this", "let's do
  this again from scratch").
- "recompute_with_overrides" — the customer explicitly asks to lock in a
  new plan with one or more changes ("lock in risk 7", "update my plan with
  ₹1 crore corpus", "save this with the new tax regime"). Must specify
  `overrides`. The result PERSISTS as the new saved plan.
- "redirect" — the customer wants something we can't handle from chat
  (specific fund swaps, goal additions, profile field edits). Set
  `redirect_reason` to a short description of what they want.

**Allowed override keys:** effective_risk_score (1–10), total_corpus (≥0),
annual_income (≥0), monthly_household_expense (≥0), emergency_fund_needed
(true/false), tax_regime ("old" or "new"). Any other override request must
fall through to "redirect" with an appropriate reason.

Distinguish counterfactual_explore (no persist, exploratory) from
recompute_with_overrides (persist as new plan) by whether the customer is
exploring vs. committing. When ambiguous, prefer counterfactual_explore.
"""

_NARRATE_SYSTEM = """You are Prozper's allocation explainer. You answer
follow-up questions about a customer's already-shown goal-based allocation
plan. Use the provided snapshot to answer. Be concise (4-8 sentences),
specific (cite numbers from the snapshot), and warm. Never invent funds
or numbers. If the question can't be answered from the snapshot, say so
and offer next steps."""

_EDUCATE_SYSTEM = """You are Prozper's allocation educator. The customer
is asking an educational question about a financial concept that appears
in their plan. Explain the concept in plain language (4-7 sentences), then
tie it back to the customer's specific holding using numbers from the
snapshot. Be accurate, never invent. If the concept doesn't appear in the
snapshot, explain it generally and note that it's not in their current mix."""

_COUNTERFACTUAL_NARRATE_SYSTEM = """You explain the result of a hypothetical
allocation calculation. Make the hypothetical-ness explicit ("this is
hypothetical, not your saved plan"). Compare to the existing plan briefly,
citing specific numbers. Keep to 4-7 sentences."""


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

@register("portfolio_optimisation")
@register("goal_planning")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Sole entry point for chat turns in this intent family."""
    last_alloc = ctx.last_agent_runs.get("goal_based_allocation")

    if last_alloc is None:
        # First turn (or no persisted snapshot in this session) → run engine.
        return await _first_turn_run_engine(ctx)

    # Follow-up turn → decide what to do.
    try:
        action = await _detect_action(last_alloc, ctx)
    except Exception as exc:
        logger.warning("detect_action failed (%s); falling back to narrate", exc)
        text = await _narrate_with_llm(last_alloc, ctx)
        return ChatHandlerResult(text=text)

    logger.info("asset_allocation_chat mode=%s overrides=%s",
                action.mode, action.overrides)
    trace_line(f"asset_allocation_chat mode={action.mode}")

    return await _dispatch_action(action, last_alloc, ctx)


# ---------------------------------------------------------------------------
# Mode dispatcher
# ---------------------------------------------------------------------------

async def _dispatch_action(
    action: ChatAction, last_alloc: AgentRunRecord, ctx: TurnContext,
) -> ChatHandlerResult:
    if action.mode == "narrate":
        text = await _narrate_with_llm(last_alloc, ctx)
        return ChatHandlerResult(text=text)

    if action.mode == "educate":
        text = await _educate_with_llm(last_alloc, ctx)
        return ChatHandlerResult(text=text)

    if action.mode == "counterfactual_explore":
        return await _counterfactual_explore(last_alloc, ctx, action.overrides or {})

    if action.mode == "clarify":
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text)

    if action.mode == "recompute_full":
        return await _recompute_full(ctx)

    if action.mode == "recompute_with_overrides":
        return await _recompute_with_overrides(ctx, action.overrides or {})

    # redirect (default)
    reason = action.redirect_reason or "change your plan"
    return ChatHandlerResult(text=_REDIRECT_TEMPLATE.format(reason=reason))


# ---------------------------------------------------------------------------
# Per-mode handlers
# ---------------------------------------------------------------------------

async def _first_turn_run_engine(ctx: TurnContext) -> ChatHandlerResult:
    """Run the engine on a fresh session (or session with no allocation yet)."""
    outcome = await compute_allocation_result(
        ctx.user_ctx, ctx.user_question,
        db=ctx.db,
        persist_recommendation=ctx.db is not None,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        spine_mode="full",
    )
    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't produce an allocation right now. Please try again."
        )
    text = format_allocation_chat_brief(outcome.result, "full")
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
    )


async def _counterfactual_explore(
    last_alloc: AgentRunRecord, ctx: TurnContext, overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides, do NOT persist, narrate as hypothetical."""
    if not overrides or not _validate_overrides(overrides):
        return ChatHandlerResult(text=_INVALID_OVERRIDE_TEMPLATE)

    user = ctx.user_ctx
    _apply_overrides(user, overrides)
    try:
        outcome = await compute_allocation_result(
            user, ctx.user_question,
            db=None,                          # NO writes
            persist_recommendation=False,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
            spine_mode="counterfactual",
        )
    finally:
        _clear_overrides(user, overrides)

    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't compute that hypothetical right now."
        )
    text = await _narrate_counterfactual(last_alloc, ctx, outcome.result, overrides)
    return ChatHandlerResult(text=text)


async def _recompute_full(ctx: TurnContext) -> ChatHandlerResult:
    """Same as first-turn but explicitly user-requested re-run."""
    return await _first_turn_run_engine(ctx)


async def _recompute_with_overrides(
    ctx: TurnContext, overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides AND persist as the new saved plan."""
    if not overrides or not _validate_overrides(overrides):
        return ChatHandlerResult(text=_INVALID_OVERRIDE_TEMPLATE)

    user = ctx.user_ctx
    _apply_overrides(user, overrides)
    try:
        outcome = await compute_allocation_result(
            user, ctx.user_question,
            db=ctx.db,                        # persist
            persist_recommendation=ctx.db is not None,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
            spine_mode="full",
        )
    finally:
        _clear_overrides(user, overrides)

    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't compute the updated plan right now."
        )
    text = format_allocation_chat_brief(outcome.result, "full")
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
    )


# ---------------------------------------------------------------------------
# Override helpers
# ---------------------------------------------------------------------------

def _validate_overrides(overrides: dict[str, Any]) -> bool:
    """All override keys must be in the allow-list."""
    return all(k in _OVERRIDE_KEY_TO_USER_ATTR for k in overrides.keys())


def _apply_overrides(user, overrides: dict[str, Any]) -> None:
    for key, val in overrides.items():
        attr = _OVERRIDE_KEY_TO_USER_ATTR.get(key)
        if attr is None:
            continue
        setattr(user, attr, val)


def _clear_overrides(user, overrides: dict[str, Any]) -> None:
    for key in overrides.keys():
        attr = _OVERRIDE_KEY_TO_USER_ATTR.get(key)
        if attr and hasattr(user, attr):
            delattr(user, attr)


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

async def _detect_action(
    last_alloc: AgentRunRecord, ctx: TurnContext,
) -> ChatAction:
    """One Haiku call returning a ChatAction."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=400,
    ).with_structured_output(ChatAction)

    snapshot = json.dumps(last_alloc.output_payload, default=str)[:6000]
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Most recent allocation snapshot (truncated):\n{snapshot}"
    )
    return await _ainvoke(llm, _DETECT_SYSTEM, user_block)


async def _narrate_with_llm(
    last_alloc: AgentRunRecord, ctx: TurnContext,
) -> str:
    return await _free_text_call(_NARRATE_SYSTEM, last_alloc, ctx)


async def _educate_with_llm(
    last_alloc: AgentRunRecord, ctx: TurnContext,
) -> str:
    return await _free_text_call(_EDUCATE_SYSTEM, last_alloc, ctx)


async def _free_text_call(
    system_text: str, last_alloc: AgentRunRecord, ctx: TurnContext,
) -> str:
    """Shared free-text Haiku call for narrate + educate."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=600,
    )
    snapshot = json.dumps(last_alloc.output_payload, default=str)
    profile = {
        "effective_risk_score": (last_alloc.input_payload or {}).get("effective_risk_score"),
        "age": (last_alloc.input_payload or {}).get("age"),
        "total_corpus": (last_alloc.input_payload or {}).get("total_corpus"),
    }
    history_lines = [
        f"{m.get('role','user')}: {m.get('content','')}"
        for m in (ctx.conversation_history or [])[-6:]
    ]
    user_block = (
        f"Snapshot:\n{snapshot}\n\n"
        f"Profile (from input): {json.dumps(profile, default=str)}\n\n"
        f"Recent history:\n" + "\n".join(history_lines) + "\n\n"
        f"Customer's current question: {ctx.user_question}"
    )
    return await _ainvoke_text(llm, system_text, user_block)


async def _narrate_counterfactual(
    last_alloc: AgentRunRecord, ctx: TurnContext,
    new_result: Any, overrides: dict[str, Any],
) -> str:
    """Narrate the hypothetical result side-by-side with the saved plan."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=500,
    )
    saved = (last_alloc.output_payload or {}).get("allocation_result", {})
    new = new_result.model_dump(mode="json") if hasattr(new_result, "model_dump") else new_result
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Overrides applied (hypothetical): {json.dumps(overrides)}\n\n"
        f"Saved plan (do NOT change this): {json.dumps(saved, default=str)}\n\n"
        f"Hypothetical result: {json.dumps(new, default=str)}\n\n"
        "Narrate the hypothetical, comparing to the saved plan. Make it "
        "clear the hypothetical is not the user's saved plan."
    )
    return await _ainvoke_text(llm, _COUNTERFACTUAL_NARRATE_SYSTEM, user_block)


# ---------------------------------------------------------------------------
# Async LangChain helpers (kept tiny so tests can patch easily)
# ---------------------------------------------------------------------------

async def _ainvoke(llm, system_text: str, user_text: str):
    """Structured-output invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    return await asyncio.to_thread(llm.invoke, messages)


async def _ainvoke_text(llm, system_text: str, user_text: str) -> str:
    """Plain-text invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    return raw.content if hasattr(raw, "content") else str(raw)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_chat.py -v
```
Expected: ~13 passed (FirstTurn 2 + Narrate 1 + Educate 1 + CounterfactualExplore 2 + Clarify 2 + RecomputeFull 1 + RecomputeWithOverrides 1 + Redirect 1 + DetectActionFailure 1).

- [ ] **Step 5: Run all earlier task tests — confirm no regression**

```bash
python3 -m pytest app/services/tests/ app/services/ai_bridge/tests/ app/services/chat_core/tests/ AI_Agents/tests/test_intent_classifier.py -v 2>&1 | tail -10
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_bridge/asset_allocation_chat.py app/services/ai_bridge/tests/test_asset_allocation_chat.py
git status   # confirm only those 2 files
git commit -m "$(cat <<'EOF'
feat: asset_allocation_chat unified handler with all 7 modes

One module owns the entire chat lifecycle for portfolio_optimisation /
goal_planning intents. First turn runs the engine; subsequent turns dispatch
through detect_action -> narrate / educate / counterfactual_explore /
clarify / recompute_full / recompute_with_overrides / redirect.

Override allow-list (6 keys) applied via transient User attributes that the
input builder reads (Task 1 wired the builder side).

Registered against both portfolio_optimisation and goal_planning via
chat_dispatcher. Brain switch to use this lands in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Brain refactor — switch to `chat_dispatcher`

**Files:**
- Modify: `app/services/chat_core/brain.py`

This is the moment the chat path actually starts using the unified module. Strip the entire `_answer_portfolio_style` method and the routing rule with `wants_fresh_recomputation`. Replace the `portfolio_optimisation`/`goal_planning` branch with a one-line dispatch.

- [ ] **Step 1: Read the current branch**

```bash
sed -n '140,180p' app/services/chat_core/brain.py
```

You should see the current branch (with the routing rule that checks `is_follow_up`, `last_alloc`, `wants_fresh_recomputation`).

- [ ] **Step 2: Replace the `portfolio_optimisation` branch**

Edit `app/services/chat_core/brain.py`. Find the existing `if intent_value in ("portfolio_optimisation", "goal_planning"):` block (~lines 142–175 in the current file). Replace the ENTIRE block with:

```python
            if intent_value in ("portfolio_optimisation", "goal_planning"):
                # Local imports — chat handler self-registers via @register at import time.
                from app.services.ai_bridge import asset_allocation_chat  # noqa: F401
                from app.services.ai_bridge.chat_dispatcher import dispatch_chat
                flow.append("dispatch_chat → asset_allocation_chat")
                trace_line("next module: chat_dispatcher → asset_allocation_chat")
                result = await dispatch_chat(intent_value, turn_context)
                return await finalize(
                    result.text,
                    ideal_allocation_snapshot_id=result.snapshot_id,
                    ideal_allocation_rebalancing_id=result.rebalancing_recommendation_id,
                )
```

- [ ] **Step 3: Delete the `_answer_portfolio_style` method**

In the same file, delete the entire `_answer_portfolio_style` async method (it lives in the `ChatBrain` class). This method is now dead code — the new branch above doesn't call it.

After deletion, also remove these unused imports if they're no longer referenced anywhere else in the file (verify each with `grep "<symbol>" app/services/chat_core/brain.py`):
- `from app.services.ai_bridge.ailax_flow import build_ailax_spine, detect_spine_mode` — `_answer_portfolio_style` was the only consumer
- Check the file for any remaining references and only remove if unused

- [ ] **Step 4: Verify imports clean**

```bash
python3 -c "from app.services.chat_core.brain import ChatBrain; print('imports clean')"
```
Expected: `imports clean`.

- [ ] **Step 5: Run all tests — confirm no regression**

```bash
python3 -m pytest app/services/tests/ app/services/ai_bridge/tests/ app/services/chat_core/tests/ AI_Agents/tests/test_intent_classifier.py -v 2>&1 | tail -10
```
Expected: all green.

- [ ] **Step 6: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`. (If 000, uvicorn is down — restart with `python3 -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 > /tmp/uvicorn.log 2>&1 &`.)

- [ ] **Step 7: Commit**

```bash
git add app/services/chat_core/brain.py
git status   # confirm only this file
git commit -m "$(cat <<'EOF'
refactor: brain dispatches portfolio chat to asset_allocation_chat (rip-and-replace)

Strips _answer_portfolio_style (~50 lines) and the wants_fresh_recomputation
routing rule. The portfolio_optimisation / goal_planning branch is now a
4-line dispatch via chat_dispatcher.dispatch_chat — the unified
asset_allocation_chat module owns first-turn engine, narrate, educate,
counterfactual, clarify, recompute, and redirect modes.

Old asset_allocation_followup module still exists but is no longer reachable
from any code path. It (and its sibling counterfactual file + the old
followup_dispatcher) get deleted in the cleanup commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Drop `wants_fresh_recomputation` from classifier

**Files:**
- Modify: `AI_Agents/src/intent_classifier/models.py`
- Modify: `AI_Agents/src/intent_classifier/classifier.py`
- Modify: `AI_Agents/src/intent_classifier/prompts.py`
- Modify: `AI_Agents/tests/test_intent_classifier.py`

The brain no longer reads `wants_fresh_recomputation` (Task 4 removed the consumption). Drop the field and the prompt section that taught the LLM to set it. The classifier now only emits intent + confidence + is_follow_up + reasoning.

- [ ] **Step 1: Drop the field from `ClassificationResult`**

Edit `AI_Agents/src/intent_classifier/models.py`. Find the `ClassificationResult` class. Remove the line:

```python
    wants_fresh_recomputation: bool = False
```

- [ ] **Step 2: Drop the field from `_LLMOutput` and the forwarding**

Edit `AI_Agents/src/intent_classifier/classifier.py`. In `_LLMOutput`, remove the entire `wants_fresh_recomputation: bool = Field(...)` block. In `classify()`, remove the line:

```python
            wants_fresh_recomputation=raw.wants_fresh_recomputation,
```

- [ ] **Step 3: Drop the prompt section**

Edit `AI_Agents/src/intent_classifier/prompts.py`. Find the `## Recomputation Detection` section (added in the earlier 13-task work). Delete it entirely — the section, its header, and any preceding `---` divider. The system prompt becomes shorter; the classifier's job is now just intent + is_follow_up + reasoning.

Also: in the `## Follow-Up Detection` section, the bullet that mentioned vague preference statements is fine to keep (it helps the classifier mark those as follow-ups, which still matters).

- [ ] **Step 4: Update tests**

Edit `AI_Agents/tests/test_intent_classifier.py`:

1. Find and DELETE the entire `WantsFreshRecomputationFieldTests` class (3 tests). The field no longer exists.
2. Find the `_FakeLLMOut` helper class. REMOVE the `wants_fresh_recomputation` parameter and the corresponding `self.wants_fresh_recomputation = ...` line.
3. Find the `_make_mock_llm_output` helper (around line 78). REMOVE the line `out.wants_fresh_recomputation = False` (it'd reference a non-existent field).

- [ ] **Step 5: Run intent classifier tests — expect pass**

```bash
python3 -m pytest AI_Agents/tests/test_intent_classifier.py -v 2>&1 | tail -10
```
Expected: all green (the 3 deleted tests are gone; remaining tests still pass).

- [ ] **Step 6: Run all earlier task tests — confirm no regression**

The classifier service test (`test_classifier_service_active_intent.py`) constructs a `MagicMock` with `wants_fresh_recomputation=False` for the return value. After this task, that field is harmlessly extra (Pydantic ignores unknown attrs on a MagicMock); no change needed. But verify:

```bash
python3 -m pytest app/services/tests/ app/services/ai_bridge/tests/ app/services/chat_core/tests/ AI_Agents/tests/test_intent_classifier.py -v 2>&1 | tail -15
```
Expected: all green.

- [ ] **Step 7: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`.

- [ ] **Step 8: Commit**

```bash
git add AI_Agents/src/intent_classifier/models.py AI_Agents/src/intent_classifier/classifier.py AI_Agents/src/intent_classifier/prompts.py AI_Agents/tests/test_intent_classifier.py
git status   # confirm only those 4 files
git commit -m "$(cat <<'EOF'
refactor: drop wants_fresh_recomputation from classifier (no longer load-bearing)

Brain no longer reads this field (replaced by asset_allocation_chat's
detect_action). Removes the field from ClassificationResult, _LLMOutput,
the SYSTEM_PROMPT's Recomputation Detection section, and all related test
plumbing. Classifier now just emits intent + confidence + is_follow_up +
reasoning.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Delete orphan files (followup module + old dispatcher + their tests)

**Files (all deleted):**
- `app/services/ai_bridge/asset_allocation_followup.py`
- `app/services/ai_bridge/asset_allocation_followup_counterfactual.py`
- `app/services/ai_bridge/followup_dispatcher.py`
- `app/services/ai_bridge/tests/test_asset_allocation_followup.py`
- `app/services/ai_bridge/tests/test_followup_dispatcher.py`

These are no longer imported by any active code path (Task 4 switched the brain to `chat_dispatcher` + `asset_allocation_chat`).

- [ ] **Step 1: Verify nothing imports the old files**

```bash
grep -rn "asset_allocation_followup\|followup_dispatcher" --include="*.py" /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/ | grep -v __pycache__ | grep -v "/tests/test_"
```

Expected output: ONLY references inside the files-to-be-deleted themselves (i.e., `asset_allocation_followup.py` self-references, etc.). No active code path should import them. If grep finds OTHER references, stop and investigate before deleting.

- [ ] **Step 2: Delete the files**

```bash
rm app/services/ai_bridge/asset_allocation_followup.py
rm app/services/ai_bridge/asset_allocation_followup_counterfactual.py
rm app/services/ai_bridge/followup_dispatcher.py
rm app/services/ai_bridge/tests/test_asset_allocation_followup.py
rm app/services/ai_bridge/tests/test_followup_dispatcher.py
```

- [ ] **Step 3: Verify imports still clean**

```bash
python3 -c "from app.services.chat_core.brain import ChatBrain; print('imports clean')"
python3 -c "from app.services.ai_bridge import asset_allocation_chat; print('asset_allocation_chat imports clean')"
```
Expected: both print "clean".

- [ ] **Step 4: Run all tests — confirm no regression**

```bash
python3 -m pytest app/services/tests/ app/services/ai_bridge/tests/ app/services/chat_core/tests/ AI_Agents/tests/test_intent_classifier.py -v 2>&1 | tail -15
```
Expected: all green.

- [ ] **Step 5: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`.

- [ ] **Step 6: Commit**

```bash
git add -A app/services/ai_bridge/asset_allocation_followup.py app/services/ai_bridge/asset_allocation_followup_counterfactual.py app/services/ai_bridge/followup_dispatcher.py app/services/ai_bridge/tests/test_asset_allocation_followup.py app/services/ai_bridge/tests/test_followup_dispatcher.py
git status   # confirm 5 deletions, nothing else
git commit -m "$(cat <<'EOF'
chore: delete orphan followup_dispatcher + asset_allocation_followup* files

Superseded by chat_dispatcher + asset_allocation_chat (the unified handler).
No active code path imports these any more after the brain switch in
the previous commit. Clean removal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: End-to-end smoke verification

This is manual verification against the running uvicorn — same shape as Task 12 from the prior plan, with the seven-mode coverage updated.

**Prereqs:**
- uvicorn running on :8000 (the previous commits' hot-reload should have absorbed all changes; if not, restart it).
- Frontend running on :8080 (or use the API directly with a JWT).
- Test user `+918888888881` with the dev DB restored.

- [ ] **Step 1: Start a fresh chat session in the UI**

In the browser at `http://localhost:8080`, log in as `8888888881`. Start a new chat session.

- [ ] **Step 2: Run the 7-mode smoke sequence**

| Turn | Message | Expected behavior |
|---|---|---|
| 1 | `Help me plan for retirement` | Full allocation deck (first-turn engine path) |
| 2 | `Is this too aggressive?` | Narrative explanation referencing snapshot numbers |
| 3 | `What does multi-cap mean and why is it in my plan?` | Educational answer that ties the concept to the user's specific holdings |
| 4 | `What if my risk score were 7?` | Hypothetical narration with explicit "this is hypothetical, not your saved plan" framing |
| 5 | `I can take more risk` | Clarification question asking for a specific value |
| 6 | `Lock in risk 7 as my plan` | Updated allocation deck (engine ran with override + persisted) |
| 7 | `Swap arbitrage for liquid funds` | Templated Profile redirect |

- [ ] **Step 3: Verify trace logs reflect the right modes**

```bash
grep -E "asset_allocation_chat mode|chat_dispatcher" /tmp/uvicorn.log | tail -20
```

Each turn should show a `mode=...` line matching the expected mode (e.g., `mode=narrate`, `mode=educate`, `mode=counterfactual_explore`, `mode=clarify`, `mode=recompute_with_overrides`, `mode=redirect`).

- [ ] **Step 4: Verify DB writes match expectations**

```bash
sqlite3 wealth_agent.db "SELECT module, output_payload IS NOT NULL AS has_output, datetime(created_at) FROM chat_ai_module_runs WHERE module='goal_based_allocation' ORDER BY created_at DESC LIMIT 5;"
```

Expected: rows from turn 1 and turn 6 only (those are the engine-running, persistence-enabled turns). Turn 4 (counterfactual_explore) and turn 5 (clarify) and turn 7 (redirect) should NOT have new rows.

- [ ] **Step 5: Optional — check at least one cross-session boundary**

Open a second chat session as the same user. Ask `Help me plan for retirement` again. Verify it runs the engine (no AgentRun for this NEW session), then verify the previous session's snapshot wasn't picked up.

If smoke passes, the architecture is working as intended. If any turn behaves differently from the table, capture the trace for that turn and let me know — we'll trace why detect_action picked the wrong mode (or whether the brain's classifier handed off to the wrong intent family).

This task is informational, not commit-bound. The final code state shipped in Task 6.

---

## Self-review checklist (run after writing the plan, before declaring done)

- [ ] **Spec coverage:** Every component in the spec maps to a task — schema migration (already shipped, no new task), engine wrapper (Task 3 imports from it), TurnContext (no change needed), classifier extension (Task 5 reverts), brain refactor (Task 4), dispatcher rename (Task 2 + Task 6), unified chat module (Task 3), input builder overrides (Task 1), tests (Tasks 1, 2, 3, 5, 6). ✓
- [ ] **No "TBD/TODO" placeholders.** ✓
- [ ] **Type/method/property consistency:**
   - `ChatHandlerResult` defined in Task 2; used in Tasks 3, 4. ✓
   - `ChatAction` Literal modes consistent across spec, Task 3 implementation, Task 3 tests. ✓
   - `_OVERRIDE_KEY_TO_USER_ATTR` keys match Task 1's transient-attribute names. ✓
   - `dispatch_chat` signature matches Task 2 definition + Task 4 brain caller. ✓
- [ ] **No reference to undefined symbols.** ✓
- [ ] **Memory constraints (no CLAUDE.md, no docs/superpowers/ commits) called out at the top.** ✓
- [ ] **Each task's tests are TDD-shaped (failing first, then implementation, then passing).** ✓
- [ ] **Each task ends with a commit step.** ✓
