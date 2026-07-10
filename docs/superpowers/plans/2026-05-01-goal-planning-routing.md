# goal_planning Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop returning allocation output for `goal_planning` questions. Route the intent to a dedicated branch in `brain.py` that returns an honest canned message; tighten the classifier's `goal_planning` vs `asset_allocation` boundary; lock the boundary with a small live eval set.

**Architecture:** Four isolated edits (no new modules). Remove the `@register("goal_planning")` decorator from the asset_allocation chat handler so the dispatcher no longer maps `goal_planning` there. Split `brain.py:124` into two branches: `asset_allocation` keeps existing dispatch; `goal_planning` returns `classification.out_of_scope_message`. Reword the `GOAL_PLANNING_MESSAGE` constant. Tighten the boundary rules in `SYSTEM_PROMPT`. Add a separate live test file with 14 boundary cases gated on `ANTHROPIC_API_KEY`.

**Tech Stack:** FastAPI, SQLAlchemy async (PostgreSQL prod, SQLite dev), Pydantic v2, LangChain + Anthropic Claude Haiku 4.5, pytest + unittest.

**Spec:** `docs/superpowers/specs/2026-05-01-goal-planning-routing-design.md`

**Run tests with:** `python3 -m pytest <path> -v` (system `python3` is what uvicorn uses; `python` is not on PATH).

**Commit format:** HEREDOC, ending with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

**Memory constraint:** Don't commit anything under `CLAUDE.md` or `docs/superpowers/`.

---

## File structure summary

| File | Action | Notes |
|---|---|---|
| `AI_Agents/src/intent_classifier/prompts.py` | Modify | Reword `GOAL_PLANNING_MESSAGE`; tighten goal_planning vs asset_allocation rules in `SYSTEM_PROMPT` |
| `app/services/ai_bridge/asset_allocation/chat.py` | Modify (line 171) | Remove `@register("goal_planning")` |
| `app/services/chat_core/brain.py` | Modify (lines 124-136) | Split `goal_planning` into its own branch returning canned message |
| `app/services/ai_bridge/tests/test_chat_dispatcher.py` | Modify | Update `RegisterImportSideEffectTests` to assert `goal_planning` is NOT registered |
| `app/services/chat_core/tests/test_brain_goal_planning.py` | **New** | Unit test for the new `goal_planning` branch in `run_turn` |
| `AI_Agents/tests/test_intent_classifier_boundary_evals.py` | **New** | Live API tests (14 cases) gated on `ANTHROPIC_API_KEY` |
| `AI_Agents/tests/test_intent_classifier.py` | No change | Existing `test_goal_planning_sets_message` still passes (constant pointer) |

---

## Task 1: Reword `GOAL_PLANNING_MESSAGE`

**Files:**
- Modify: `AI_Agents/src/intent_classifier/prompts.py:229-233`

**Spec ref:** Design §2 ("Reword `GOAL_PLANNING_MESSAGE`").

The new wording must (per spec): acknowledge the gap, offer an allocation-shaped pivot when the user has money in hand, stay short, avoid promising a date, avoid sending users to the Profile section as a workaround.

- [ ] **Step 1: Verify the existing constant test still passes (sanity)**

Run: `python3 -m pytest AI_Agents/tests/test_intent_classifier.py::TestIntentClassifier::test_goal_planning_sets_message -v`
Expected: PASS

- [ ] **Step 2: Replace the constant**

In `AI_Agents/src/intent_classifier/prompts.py`, replace the existing block:

```python
GOAL_PLANNING_MESSAGE = (
    "Goal planning is coming soon to Prozpr! In the meantime, please head over to your "
    "Profile section and update your financial goals there — that way we'll have everything "
    "ready to give you a personalised plan the moment the feature goes live."
)
```

with:

```python
GOAL_PLANNING_MESSAGE = (
    "Goal planning — checking whether a target like '₹5 crore in 15 years' is "
    "achievable, and what monthly investment would get you there — is something "
    "we're actively building. I can't run that math for you yet.\n\n"
    "If you'd like, tell me how much you have to invest (a lump sum, or a monthly "
    "amount) and your time horizon, and I can suggest an allocation that fits. "
    "Once goal planning is live, I'll be able to tell you whether the target is "
    "reachable and what it would take."
)
```

- [ ] **Step 3: Run the constant-pointer test (must still pass)**

Run: `python3 -m pytest AI_Agents/tests/test_intent_classifier.py::TestIntentClassifier::test_goal_planning_sets_message -v`
Expected: PASS (the test compares the result field to `GOAL_PLANNING_MESSAGE` by reference, so wording doesn't matter).

- [ ] **Step 4: Run the full classifier suite**

Run: `python3 -m pytest AI_Agents/tests/test_intent_classifier.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/intent_classifier/prompts.py
git commit -m "$(cat <<'EOF'
feat(intent_classifier): reword GOAL_PLANNING_MESSAGE for honest gap + useful pivot

The previous copy redirected users to update goals in the Profile section,
which doesn't actually help them with feasibility questions. New copy
acknowledges the missing capability and offers an allocation-shaped pivot
for users who have money in hand.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Tighten classifier `goal_planning` vs `asset_allocation` boundary

**Files:**
- Modify: `AI_Agents/src/intent_classifier/prompts.py` (the `SYSTEM_PROMPT` constant — sections "### 1. asset_allocation", "### 2. goal_planning", "## Classification Rules")

**Spec ref:** Design §3 ("Tighten classifier prompt boundary").

The change formalises three rules from the spec:
1. `goal_planning` triggers when the *primary ask* is feasibility / required-savings.
2. `asset_allocation` triggers when the primary ask is where to put money — even if a goal is mentioned as context.
3. Tie-breaker: a question that asks BOTH ("at ₹50k/month, can I hit ₹10cr in 15y, and where should I invest?") classifies as `goal_planning` — the feasibility component is what we cannot answer well, and the redirect is more honest than a partial allocation.

- [ ] **Step 1: Update the `### 1. asset_allocation` section**

Find the existing block (lines 9–27 in current file) starting with:

```
### 1. asset_allocation
The customer wants to **take action** on their own portfolio
```

Append the following paragraph immediately after the "Example questions" block of that section (before the `---` separator):

```
**Goal-mention does not flip intent.** A question that mentions a goal as context but whose primary ask is "where should I invest" stays in `asset_allocation`. Examples:
- "I have ₹50k/month and want ₹10 crore in 15 years — where should I invest?" → `asset_allocation` (primary ask is allocation; goal is context)
- "Should I add midcap to my portfolio for my retirement goal?" → `asset_allocation`
```

- [ ] **Step 2: Update the `### 2. goal_planning` section**

Replace the existing block (current lines 30–42) with:

```
### 2. goal_planning
The customer's **primary ask is feasibility, achievability, or required-savings math** — questions whose natural answer is a number or a yes/no about whether a future target is reachable. The hallmark is that the answer requires running future-value math (and possibly probability bands), not producing an allocation.

Triggers when the customer is asking:
- Whether a future financial target (retirement corpus, child's education, house down-payment, vacation, car, emergency fund) is achievable on their current trajectory
- How much they need to save / invest each month to reach a target by a date
- What corpus they will end up with given a current SIP and horizon
- Whether their current savings rate is sufficient to meet a goal

Example questions:
- "I want to retire in 15 years with ₹5 crore — is that possible?"
- "How much do I need to save monthly for my daughter's college in 10 years?"
- "At my current ₹50k/month SIP, what corpus will I have in 20 years?"
- "Will my current SIP be enough to hit ₹2 crore by 2040?"

Key distinction from asset_allocation: `asset_allocation` answers **"where should I put my money?"**; `goal_planning` answers **"is the target reachable, and what does it take?"**. A goal mention alone does not flip the intent — only a feasibility / required-savings ask does.
```

- [ ] **Step 3: Add the tie-breaker rule to `## Classification Rules`**

Find the "## Classification Rules" section (around line 209). Insert this bullet immediately after the existing bullet about stock_advice ("Direct stock pick questions … always go to `stock_advice`, not `asset_allocation`."):

```
- If a question contains BOTH a feasibility / required-savings ask AND an allocation ask ("at ₹50k/month, can I hit ₹10cr in 15 years, and where should I invest?"), classify as `goal_planning`. The feasibility component is the part that requires math we cannot yet do well; the honest redirect is better than a partial allocation answer that ignores the feasibility question.
```

- [ ] **Step 4: Run the classifier mock suite (sanity, no behaviour change at the mock level)**

Run: `python3 -m pytest AI_Agents/tests/test_intent_classifier.py -v`
Expected: all PASS (mocks don't exercise the prompt text; this just confirms no syntax error in the prompt file).

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/intent_classifier/prompts.py
git commit -m "$(cat <<'EOF'
feat(intent_classifier): tighten goal_planning vs asset_allocation boundary

Make explicit that goal mentions don't flip intent — only feasibility or
required-savings asks do. Add a tie-breaker for combined questions: when a
message asks both "is X achievable" and "where should I invest", classify as
goal_planning so the user gets an honest "feasibility math is coming"
redirect instead of a partial allocation that ignores feasibility.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Remove `@register("goal_planning")` from asset_allocation chat handler

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation/chat.py:170-172` (decorator + module docstring)

**Spec ref:** Design §1 ("Router fix") — paired with Task 4. Done first because the dispatcher-side change must land before brain.py stops calling `dispatch_chat("goal_planning", …)`, otherwise an interleaved deploy could route `goal_planning` to nowhere.

**Why ordering matters:** if Task 4 (brain split) lands first while `goal_planning` is still registered, behaviour is unchanged. If Task 3 lands first while brain.py still routes `goal_planning` through `dispatch_chat`, every `goal_planning` turn raises `RuntimeError: No chat handler registered`. Both orderings keep the system working only if Task 3 and Task 4 are part of the same commit, OR the test suite runs between commits to catch the gap. **Solution: commit Task 3 + Task 4 together, in a single commit, after both edits are made.** Run the dispatcher tests AFTER both edits.

- [ ] **Step 1: Edit chat.py — remove the decorator and update the docstring**

In `app/services/ai_bridge/asset_allocation/chat.py`:

Replace the file's module docstring at the top (lines 1–12):

```python
"""Unified chat handler for asset_allocation / goal_planning intents.

Single entry point for the entire chat lifecycle of allocation conversations:
- First turn (no AgentRun for asset_allocation in session) → run engine,
  persist, return chat brief
- Subsequent turns → call _detect_action LLM to pick one of 7 modes
  (narrate / educate / counterfactual_explore / clarify / recompute_full /
   recompute_with_overrides / redirect), then dispatch.

The engine wrapper compute_allocation_result lives in ``service.py`` (sibling
module) and is consumed by both this module and the standalone HTTP endpoint.
"""
```

with:

```python
"""Chat handler for the asset_allocation intent.

Single entry point for the entire chat lifecycle of allocation conversations:
- First turn (no AgentRun for asset_allocation in session) → run engine,
  persist, return chat brief
- Subsequent turns → call _detect_action LLM to pick one of 7 modes
  (narrate / educate / counterfactual_explore / clarify / recompute_full /
   recompute_with_overrides / redirect), then dispatch.

The engine wrapper compute_allocation_result lives in ``service.py`` (sibling
module) and is consumed by both this module and the standalone HTTP endpoint.

Note: this handler is registered ONLY for the asset_allocation intent.
The goal_planning intent is handled in app/services/chat_core/brain.py via a
canned redirect (no agent module exists for goal_planning yet).
"""
```

Then, at lines 170–172, replace:

```python
@register("asset_allocation")
@register("goal_planning")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
```

with:

```python
@register("asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
```

- [ ] **Step 2: Do not run tests yet** — defer until Task 4 also lands. The `RegisterImportSideEffectTests` currently asserts `goal_planning` IS registered, which would now fail. We update both in one commit.

---

## Task 4: Split `goal_planning` into its own branch in `brain.py`

**Files:**
- Modify: `app/services/chat_core/brain.py:124-136`

**Spec ref:** Design §1 ("Router fix").

- [ ] **Step 1: Edit brain.py — split the branch**

In `app/services/chat_core/brain.py`, replace the existing block at lines 124–136:

```python
            if intent_value in ("asset_allocation", "goal_planning"):
                # Local imports — chat handler self-registers via @register at import time.
                # Local imports — chat handler self-registers via @register at import time.
                from app.services.ai_bridge.asset_allocation import chat as _aa_chat  # noqa: F401
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

with:

```python
            if intent_value == "asset_allocation":
                # Local import — chat handler self-registers via @register at import time.
                from app.services.ai_bridge.asset_allocation import chat as _aa_chat  # noqa: F401
                from app.services.ai_bridge.chat_dispatcher import dispatch_chat
                flow.append("dispatch_chat → asset_allocation_chat")
                trace_line("next module: chat_dispatcher → asset_allocation_chat")
                result = await dispatch_chat(intent_value, turn_context)
                return await finalize(
                    result.text,
                    ideal_allocation_snapshot_id=result.snapshot_id,
                    ideal_allocation_rebalancing_id=result.rebalancing_recommendation_id,
                )

            if intent_value == "goal_planning":
                # No agent module yet — return the canned redirect attached
                # by the classifier. When the goal_planning module ships,
                # replace this branch with a dispatch_chat("goal_planning", ...) call.
                flow.append("goal_planning → canned redirect (module not yet built)")
                trace_line("next module: goal_planning → canned redirect")
                redirect_text = (
                    classification.out_of_scope_message
                    or "Goal planning isn't available yet — please ask me about your portfolio or where to invest."
                )
                return await finalize(redirect_text)
```

The `or "..."` fallback covers the (defensive) case where the classifier returns `goal_planning` without populating `out_of_scope_message`. Today the classifier always populates it (`classifier.py:122`), but the fallback prevents `finalize(None)`.

- [ ] **Step 2: Verify the spec's expected behaviour by reading the diff**

Run: `git diff app/services/chat_core/brain.py app/services/ai_bridge/asset_allocation/chat.py`
Expected: shows the brain.py split + the `chat.py` decorator/docstring change from Task 3.

- [ ] **Step 3: Update `RegisterImportSideEffectTests` (next task) BEFORE running tests** — proceed to Task 5 immediately, then come back here for Step 4.

- [ ] **Step 4: After Task 5 lands, run the dispatcher suite to confirm**

Run: `python3 -m pytest app/services/ai_bridge/tests/test_chat_dispatcher.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit Task 3 + Task 4 + Task 5 together** — see Task 5's final step.

---

## Task 5: Update `RegisterImportSideEffectTests` to reflect the new registration

**Files:**
- Modify: `app/services/ai_bridge/tests/test_chat_dispatcher.py:59-89`

**Spec ref:** Design §1 — necessary corollary of Task 3.

This test currently asserts that importing `asset_allocation/chat.py` registers BOTH `asset_allocation` AND `goal_planning`. After Task 3, only `asset_allocation` should register.

- [ ] **Step 1: Replace the test class**

In `app/services/ai_bridge/tests/test_chat_dispatcher.py`, replace the entire `RegisterImportSideEffectTests` block (lines 59–89):

```python
class RegisterImportSideEffectTests(unittest.TestCase):
    """Importing asset_allocation_chat must populate the dispatcher registry.

    Locks the import-as-side-effect contract: removing the @register decorators
    on handle() OR removing the brain.py
    `from app.services.ai_bridge import asset_allocation_chat` import would
    silently break portfolio chat. Without this test, a future cleanup of the
    noqa: F401 import in brain.py would cause every portfolio turn to fall
    through to the safe-fallback canned message with no test signal.
    """

    def test_importing_asset_allocation_chat_registers_both_intents(self):
        import importlib
        from app.services.ai_bridge import chat_dispatcher as cd

        # Clear and force a fresh import so the @register decorators run again.
        cd._HANDLERS.clear()
        from app.services.ai_bridge.asset_allocation import chat as asset_allocation_chat
        importlib.reload(asset_allocation_chat)

        self.assertIn("asset_allocation", cd._HANDLERS)
        self.assertIn("goal_planning", cd._HANDLERS)
        # Both intents resolve to the same public handler.
        self.assertIs(
            cd._HANDLERS["asset_allocation"],
            asset_allocation_chat.handle,
        )
        self.assertIs(
            cd._HANDLERS["goal_planning"],
            asset_allocation_chat.handle,
        )
```

with:

```python
class RegisterImportSideEffectTests(unittest.TestCase):
    """Importing asset_allocation_chat must populate the dispatcher registry.

    Locks the import-as-side-effect contract: removing the @register decorator
    on handle() OR removing the brain.py
    `from app.services.ai_bridge.asset_allocation import chat as _aa_chat` import
    would silently break portfolio chat. Without this test, a future cleanup of
    the noqa: F401 import in brain.py would cause every portfolio turn to fall
    through to the safe-fallback canned message with no test signal.

    NOTE: as of the goal_planning routing fix, this handler is registered ONLY
    for asset_allocation. goal_planning is handled by a dedicated branch in
    brain.py that returns the classifier's canned message — it is intentionally
    NOT in the dispatcher registry. If a future change re-adds the
    @register("goal_planning") decorator to chat.py, the goal_planning branch
    in brain.py will be silently bypassed.
    """

    def test_importing_asset_allocation_chat_registers_only_asset_allocation(self):
        import importlib
        from app.services.ai_bridge import chat_dispatcher as cd

        # Clear and force a fresh import so the @register decorators run again.
        cd._HANDLERS.clear()
        from app.services.ai_bridge.asset_allocation import chat as asset_allocation_chat
        importlib.reload(asset_allocation_chat)

        # asset_allocation is registered.
        self.assertIn("asset_allocation", cd._HANDLERS)
        self.assertIs(
            cd._HANDLERS["asset_allocation"],
            asset_allocation_chat.handle,
        )
        # goal_planning is NOT registered — it is handled in brain.py via canned redirect.
        self.assertNotIn("goal_planning", cd._HANDLERS)
```

- [ ] **Step 2: Run the dispatcher suite**

Run: `python3 -m pytest app/services/ai_bridge/tests/test_chat_dispatcher.py -v`
Expected: all 5 tests PASS (4 from `ChatDispatcherTests` + 1 renamed `test_importing_asset_allocation_chat_registers_only_asset_allocation`).

- [ ] **Step 3: Commit Task 3 + Task 4 + Task 5 as a single commit**

```bash
git add app/services/ai_bridge/asset_allocation/chat.py \
        app/services/chat_core/brain.py \
        app/services/ai_bridge/tests/test_chat_dispatcher.py
git commit -m "$(cat <<'EOF'
fix(chat): route goal_planning to canned redirect instead of allocation

Pure feasibility questions like "I want to retire in 15 years with ₹5cr —
is that possible?" were silently routed to the asset_allocation handler,
which produced an allocation recommendation instead of answering the
feasibility question (no feasibility math exists yet).

This commit:
- Removes @register("goal_planning") from the asset_allocation chat handler
- Adds a dedicated goal_planning branch in brain.run_turn that returns the
  classifier's canned out_of_scope_message
- Updates the dispatcher import-side-effect test to assert that
  goal_planning is intentionally NOT registered

When the real goal_planning agent module ships, replace the brain.py branch
with a dispatch_chat("goal_planning", ...) call — no classifier or prompt
changes needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add a `brain.py` unit test for the new `goal_planning` branch

**Files:**
- Create: `app/services/chat_core/tests/__init__.py` (empty, only if not present)
- Create: `app/services/chat_core/tests/test_brain_goal_planning.py`

**Spec ref:** Design §1 — provides direct test coverage for the new branch (Task 4 changed live behaviour but only the dispatcher-import test signals the registration change; we want a positive assertion that brain.py returns the canned message and does NOT call `dispatch_chat`).

- [ ] **Step 1: Ensure the tests directory exists**

```bash
mkdir -p app/services/chat_core/tests
ls app/services/chat_core/tests/__init__.py 2>/dev/null || touch app/services/chat_core/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `app/services/chat_core/tests/test_brain_goal_planning.py`:

```python
"""brain.run_turn: goal_planning branch returns the canned redirect."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat_core.brain import ChatBrain
from app.services.chat_core.types import ChatTurnInput


def _make_turn() -> ChatTurnInput:
    return ChatTurnInput(
        effective_user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        db=None,
        user_question="I want to retire in 15 years with 5 crore — is that possible?",
        conversation_history=[],
        user_ctx=MagicMock(),
        client_context=None,
    )


class BrainGoalPlanningBranchTests(unittest.IsolatedAsyncioTestCase):

    async def test_goal_planning_returns_canned_message_and_does_not_dispatch(self):
        canned = "Goal planning is coming — ask me about allocation in the meantime."

        # Mock classification result
        classification = MagicMock()
        classification.intent.value = "goal_planning"
        classification.confidence = 0.93
        classification.reasoning = "Customer asking feasibility question."
        classification.out_of_scope_message = canned

        # Mock turn context
        fake_turn_context = MagicMock()
        fake_turn_context.last_agent_runs = {}
        fake_turn_context.active_intent = None

        with patch(
            "app.services.chat_core.brain.build_turn_context",
            new=AsyncMock(return_value=fake_turn_context),
        ), patch(
            "app.services.chat_core.brain.classify_user_message",
            new=AsyncMock(return_value=classification),
        ), patch(
            "app.services.chat_core.brain.log_chat_turn_flow_summary",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
            new=AsyncMock(),
        ) as mock_dispatch:
            result = await ChatBrain().run_turn(_make_turn())

        self.assertEqual(result.content, canned)
        self.assertEqual(result.intent, "goal_planning")
        mock_dispatch.assert_not_called()

    async def test_goal_planning_falls_back_when_canned_message_missing(self):
        # Defensive: classifier returns goal_planning without populating
        # out_of_scope_message. The brain branch must still produce a string.
        classification = MagicMock()
        classification.intent.value = "goal_planning"
        classification.confidence = 0.5
        classification.reasoning = "low-confidence goal classification."
        classification.out_of_scope_message = None

        fake_turn_context = MagicMock()
        fake_turn_context.last_agent_runs = {}
        fake_turn_context.active_intent = None

        with patch(
            "app.services.chat_core.brain.build_turn_context",
            new=AsyncMock(return_value=fake_turn_context),
        ), patch(
            "app.services.chat_core.brain.classify_user_message",
            new=AsyncMock(return_value=classification),
        ), patch(
            "app.services.chat_core.brain.log_chat_turn_flow_summary",
            new=AsyncMock(return_value=None),
        ):
            result = await ChatBrain().run_turn(_make_turn())

        self.assertIsInstance(result.content, str)
        self.assertGreater(len(result.content), 0)
        self.assertEqual(result.intent, "goal_planning")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the new test file**

Run: `python3 -m pytest app/services/chat_core/tests/test_brain_goal_planning.py -v`
Expected: both tests PASS.

If `ChatTurnInput` rejects the kwargs above (the type may differ), inspect `app/services/chat_core/types.py` and adjust the constructor call to match the real signature. The test logic stays identical.

- [ ] **Step 4: Commit**

```bash
git add app/services/chat_core/tests/__init__.py \
        app/services/chat_core/tests/test_brain_goal_planning.py
git commit -m "$(cat <<'EOF'
test(brain): cover goal_planning branch returning canned redirect

Direct unit coverage for the new branch added in the prior commit. Asserts
that goal_planning intents return classification.out_of_scope_message and
do NOT invoke dispatch_chat. Also covers the defensive fallback when the
classifier omits out_of_scope_message.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Add live boundary eval set

**Files:**
- Create: `AI_Agents/tests/test_intent_classifier_boundary_evals.py`

**Spec ref:** Design §4 ("Boundary eval set").

These tests call the live Claude Haiku classifier with the tightened prompt. They are gated on `ANTHROPIC_API_KEY` so they no-op in CI environments without credentials. The acceptance threshold is at least 12 of 14 cases passing (~85%).

- [ ] **Step 1: Create the new test file**

Create `AI_Agents/tests/test_intent_classifier_boundary_evals.py`:

```python
"""Live boundary evals: goal_planning vs asset_allocation.

These tests call the real Claude Haiku classifier and are skipped when
ANTHROPIC_API_KEY is not present in the environment. They exist to lock the
prompt's intent boundary defined in the design spec
docs/superpowers/specs/2026-05-01-goal-planning-routing-design.md §3.

Run manually:
    ANTHROPIC_API_KEY=sk-... python3 -m pytest \
        AI_Agents/tests/test_intent_classifier_boundary_evals.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

# Make AI_Agents/src importable when running from the repo root.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from intent_classifier import (  # noqa: E402
    ClassificationInput,
    Intent,
    IntentClassifier,
)


# (question, expected intent, label) — 14 cases covering the spec's boundary categories.
BOUNDARY_CASES: list[tuple[str, Intent, str]] = [
    # Pure feasibility (no money hook) → goal_planning
    ("I want to retire in 15 years with 5 crore — is that possible?",
     Intent.GOAL_PLANNING, "feasibility-only-retirement"),
    ("Will my current SIP be enough to hit 2 crore by 2040?",
     Intent.GOAL_PLANNING, "feasibility-only-trajectory"),
    ("Can I afford a 1cr house down-payment in 7 years?",
     Intent.GOAL_PLANNING, "feasibility-only-house"),

    # Required savings → goal_planning
    ("How much should I save each month for my daughter's college in 10 years?",
     Intent.GOAL_PLANNING, "required-savings-college"),
    ("How much do I need to invest monthly to retire with 5 crore in 20 years?",
     Intent.GOAL_PLANNING, "required-savings-retirement"),

    # Money-in-hand with goal mention (allocation primary) → asset_allocation
    ("I have 10 lakh to invest for my retirement in 20 years — where should I put it?",
     Intent.ASSET_ALLOCATION, "money-in-hand-with-goal-lump-sum"),
    ("I can do 50k a month for my daughter's college in 12 years — how should I invest it?",
     Intent.ASSET_ALLOCATION, "money-in-hand-with-goal-monthly"),
    ("Should I add midcap to my portfolio for my retirement goal?",
     Intent.ASSET_ALLOCATION, "portfolio-with-goal-mention"),

    # Where-to-invest with no goal → asset_allocation
    ("I have 5 lakh to invest — where should I put it?",
     Intent.ASSET_ALLOCATION, "where-to-invest-no-goal"),
    ("Should I switch from Axis Bluechip to Mirae Asset Large Cap?",
     Intent.ASSET_ALLOCATION, "fund-switch"),

    # Combined feasibility + allocation → goal_planning (tie-breaker)
    ("At 50k a month, can I hit 10cr in 15 years, and where should I invest it?",
     Intent.GOAL_PLANNING, "combined-feasibility-and-allocation"),
    ("Will my 30k SIP get me to 3 crore in 18 years, and what mix should I use?",
     Intent.GOAL_PLANNING, "combined-trajectory-and-mix"),

    # Adversarial: ordering bias — allocation phrasing first, goal at the end
    ("Where should I invest my 50k monthly to retire with 5 crore in 15 years?",
     Intent.ASSET_ALLOCATION, "ordering-allocation-first"),

    # Adversarial: feasibility phrased as a question about achievability with no money mention
    ("Is 1 crore in 10 years a realistic target for me?",
     Intent.GOAL_PLANNING, "adversarial-realistic-target"),
]


@unittest.skipUnless(
    os.getenv("ANTHROPIC_API_KEY"),
    "ANTHROPIC_API_KEY not set — skipping live classifier boundary evals.",
)
class GoalPlanningBoundaryEvals(unittest.TestCase):
    """Live evals; require Anthropic credentials."""

    @classmethod
    def setUpClass(cls):
        cls.classifier = IntentClassifier()

    def test_boundary_cases_meet_threshold(self):
        passes: list[str] = []
        failures: list[tuple[str, str, str, str]] = []  # (label, question, expected, actual)

        for question, expected, label in BOUNDARY_CASES:
            result = self.classifier.classify(
                ClassificationInput(customer_question=question)
            )
            if result.intent == expected:
                passes.append(label)
            else:
                failures.append((label, question, expected.value, result.intent.value))

        total = len(BOUNDARY_CASES)
        threshold = 12  # 12 / 14 ≈ 86%
        msg_lines = [f"Boundary eval: {len(passes)} / {total} passed."]
        for label, q, exp, got in failures:
            msg_lines.append(f"  - [{label}] expected={exp} got={got} :: {q!r}")
        msg = "\n".join(msg_lines)

        # Always print the result line so manual runs surface the score.
        print("\n" + msg)

        self.assertGreaterEqual(
            len(passes), threshold,
            f"Boundary eval below threshold ({len(passes)}/{total} < {threshold}).\n{msg}",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the boundary evals (requires `ANTHROPIC_API_KEY`)**

Run: `python3 -m pytest AI_Agents/tests/test_intent_classifier_boundary_evals.py -v -s`
Expected (with API key): PASS — at least 12 of 14 cases. Output shows the full score line and any individual failures.
Expected (without API key): SKIPPED — single skip message.

If the threshold isn't met, investigate which cases fail. Two failure modes:
- **Goal-flavoured questions classified as `asset_allocation`** → tighten the `### 2. goal_planning` section of the system prompt to lean harder on "feasibility / required-savings is the primary ask, even with money mentioned" — only for cases where the question form is "is X possible" or "how much do I need".
- **Allocation questions classified as `goal_planning`** → the new tie-breaker rule may be over-firing; tighten the rule's wording or add a counterexample to the asset_allocation section.

Do NOT lower the threshold to make the test pass. Iterate on the prompt instead.

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/tests/test_intent_classifier_boundary_evals.py
git commit -m "$(cat <<'EOF'
test(intent_classifier): add live boundary eval set for goal_planning

14 cases covering feasibility-only, required-savings, money-in-hand-with-goal,
where-to-invest, combined feasibility+allocation, and two adversarial
orderings. Gated on ANTHROPIC_API_KEY so CI without credentials skips the
file. Threshold: 12/14 (~86%) — fail loud below that.

Used to lock the goal_planning vs asset_allocation classifier boundary
introduced in the prior commit. Run manually after any change to
SYSTEM_PROMPT in AI_Agents/src/intent_classifier/prompts.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the full intent_classifier suite**

Run: `python3 -m pytest AI_Agents/tests/test_intent_classifier.py -v`
Expected: all PASS.

- [ ] **Step 2: Run the chat dispatcher + brain unit tests**

Run: `python3 -m pytest app/services/ai_bridge/tests/test_chat_dispatcher.py app/services/chat_core/tests/test_brain_goal_planning.py -v`
Expected: all PASS.

- [ ] **Step 3: Run the live boundary evals (manual, requires API key)**

Run: `ANTHROPIC_API_KEY=<key> python3 -m pytest AI_Agents/tests/test_intent_classifier_boundary_evals.py -v -s`
Expected: ≥ 12/14 cases PASS.

- [ ] **Step 4: Manual smoke test**

In a dev shell, send the following user question through the live chat path: *"I want to retire in 15 years with ₹5 crore — is that possible?"*. Confirm the response is the new `GOAL_PLANNING_MESSAGE` (no allocation chart, no clarify question, no portfolio recompute).

If the response is still an allocation, the most likely cause is that the running uvicorn process predates the brain.py change — restart the server.

- [ ] **Step 5: Verify spec coverage**

| Spec section | Implementing task |
|---|---|
| §1 Router fix | Tasks 3 + 4 + 5 (single commit) |
| §1 Remove `@register("goal_planning")` | Task 3 |
| §2 Reword `GOAL_PLANNING_MESSAGE` | Task 1 |
| §3 Tighten classifier boundary | Task 2 |
| §4 Boundary eval set (10–20 cases) | Task 7 (14 cases, threshold 12) |
| Architecture overview | Tasks 3+4+5 collectively realise the new router shape |
| Open question: low-confidence fallback | Resolved per spec default — always redirect (no code) |
| Open question: portfolio-aware message variants | Resolved per spec default — single static message (no code) |

All spec sections covered. No placeholders.
