# asset_allocation_followup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the bug where follow-up questions on a previously-shown allocation re-run the engine instead of narrating the existing snapshot. Establish a per-module followup pattern that future modules (rebalancing, market commentary, etc.) inherit.

**Architecture:** Persist structured `AgentRun` records (input + output JSONB) on `chat_ai_module_runs`. Build a per-turn `TurnContext` with the last AgentRun per module. Extend the intent classifier to emit `wants_fresh_recomputation`. Brain routes follow-ups (`is_follow_up AND last_agent_run AND NOT wants_fresh_recomputation`) to a thin `followup_dispatcher` which calls per-module handlers — `asset_allocation_followup` is the only handler this iteration. Handler supports narrate, counterfactual (risk-score override only, no persistence), and mutation-redirect.

**Tech Stack:** FastAPI, SQLAlchemy async (PostgreSQL JSONB on prod, JSON on dev SQLite), Alembic, Pydantic v2, LangChain + Anthropic Claude Haiku 4.5, pytest + unittest.

**Spec:** `docs/superpowers/specs/2026-04-27-asset-allocation-followup-design.md`

**Run tests with:** `python3 -m pytest <path> -v` (the system `python3` is what uvicorn uses; `python` is not on PATH).

---

## Task 1: Schema migration + ORM — add `input_payload` and `output_payload` columns

**Files:**
- Create: `alembic/versions/<auto>_add_payload_columns_to_chat_ai_module_runs.py`
- Modify: `app/models/chat_ai_module_run.py:42`

- [ ] **Step 1: Generate the empty Alembic migration**

Run from `Prozpr_Backend/`:
```bash
alembic revision -m "add payload columns to chat_ai_module_runs"
```
Expected: prints `Generating alembic/versions/<hash>_add_payload_columns_to_chat_ai_module_runs.py ... done`. Note the hash for the next step.

- [ ] **Step 2: Fill in the migration**

Open the new file. Replace the auto-generated body with:

```python
"""Add payload columns to chat_ai_module_runs.

Revision ID: <hash>
Revises: f7c91d2e4a00
Create Date: 2026-04-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "<hash>"  # leave as the auto-generated value
down_revision: Union[str, None] = "f7c91d2e4a00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_ai_module_runs",
        sa.Column("input_payload", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "chat_ai_module_runs",
        sa.Column("output_payload", postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "ix_chat_ai_module_runs_session_module_created",
        "chat_ai_module_runs",
        ["session_id", "module", sa.text("created_at DESC")],
        postgresql_where=sa.text("output_payload IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_ai_module_runs_session_module_created", table_name="chat_ai_module_runs")
    op.drop_column("chat_ai_module_runs", "output_payload")
    op.drop_column("chat_ai_module_runs", "input_payload")
```

The partial index speeds up the "last AgentRun per module per session" query in `TurnContext`.

- [ ] **Step 3: Update the ORM model**

Edit `app/models/chat_ai_module_run.py` — add two fields after the existing `extra` field at line 42:

```python
    extra: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    input_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    output_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 4: Apply the migration**

Run from `Prozpr_Backend/`:
```bash
alembic upgrade head
```
Expected: prints `Running upgrade f7c91d2e4a00 -> <hash>, add payload columns to chat_ai_module_runs`.

- [ ] **Step 5: Verify the columns exist**

```bash
python3 -c "
from app.models.chat_ai_module_run import ChatAiModuleRun
print([c.name for c in ChatAiModuleRun.__table__.columns])
"
```
Expected output includes `input_payload` and `output_payload`.

- [ ] **Step 6: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/<filename> app/models/chat_ai_module_run.py
git commit -m "feat: add input_payload + output_payload columns to chat_ai_module_runs

Lays the groundwork for persisted AgentRun records (structured input/output
per agent invocation). Existing telemetry callers continue writing rows
with these columns NULL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Extend `record_ai_module_run` to accept payload kwargs

**Files:**
- Modify: `app/services/ai_module_telemetry.py:20-61`
- Create: `app/services/tests/__init__.py` (empty)
- Create: `app/services/tests/test_ai_module_telemetry.py`

- [ ] **Step 1: Create the test directory marker**

```bash
mkdir -p app/services/tests
touch app/services/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `app/services/tests/test_ai_module_telemetry.py`:

```python
"""Unit tests for record_ai_module_run payload kwargs."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.services.ai_module_telemetry import record_ai_module_run


class RecordAiModuleRunPayloadTests(unittest.TestCase):

    def test_payload_kwargs_persisted_on_row(self):
        """input_payload and output_payload are written when passed in."""
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        asyncio.run(record_ai_module_run(
            db,
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            module="goal_based_allocation",
            reason="full_pipeline_run",
            input_payload={"corpus": 8_000_000},
            output_payload={"allocation_result": {"grand_total": 8_000_000}},
            emit_standard_log=False,
        ))

        self.assertEqual(len(added), 1)
        row = added[0]
        self.assertEqual(row.input_payload, {"corpus": 8_000_000})
        self.assertEqual(row.output_payload, {"allocation_result": {"grand_total": 8_000_000}})

    def test_omitted_payload_kwargs_default_to_none(self):
        """Existing callers (no payload kwargs) keep persisting NULLs."""
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        asyncio.run(record_ai_module_run(
            db,
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            module="chat_flow",
            reason="some flow summary",
            emit_standard_log=False,
        ))

        self.assertEqual(len(added), 1)
        row = added[0]
        self.assertIsNone(row.input_payload)
        self.assertIsNone(row.output_payload)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test — expect failure**

```bash
python3 -m pytest app/services/tests/test_ai_module_telemetry.py -v
```
Expected: FAIL — `TypeError: record_ai_module_run() got an unexpected keyword argument 'input_payload'`.

- [ ] **Step 4: Implement — extend `record_ai_module_run`**

Edit `app/services/ai_module_telemetry.py`. Replace the function signature and body of `record_ai_module_run` (lines 20–61) with:

```python
async def record_ai_module_run(
    db: AsyncSession | None,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    module: str,
    reason: str,
    intent_detected: str | None = None,
    spine_mode: str | None = None,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    emit_standard_log: bool = True,
) -> uuid.UUID | None:
    """
    Optionally emit AILAX_AI_MODULE_RUN; always persist one row when db is set.
    Use emit_standard_log=False when a higher-level AILAX_CHAT_FLOW line is logged instead.
    Returns the new row's id (or None when db is None).
    """
    if emit_standard_log:
        logger.info(
            "AILAX_AI_MODULE_RUN module=%s reason=%s user_id=%s session_id=%s intent=%s spine_mode=%s duration_ms=%s",
            module,
            reason.replace("\n", " ")[:500],
            user_id,
            session_id,
            intent_detected,
            spine_mode,
            duration_ms,
        )
    if db is None:
        return None
    row = ChatAiModuleRun(
        user_id=user_id,
        session_id=session_id,
        module=module,
        reason=reason,
        intent_detected=intent_detected,
        spine_mode=spine_mode,
        duration_ms=duration_ms,
        extra=extra,
        input_payload=input_payload,
        output_payload=output_payload,
    )
    db.add(row)
    await db.flush()
    return row.id
```

`log_chat_turn_flow_summary` already calls `record_ai_module_run` and ignores the return — no change needed there.

- [ ] **Step 5: Run the test — expect pass**

```bash
python3 -m pytest app/services/tests/test_ai_module_telemetry.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_module_telemetry.py app/services/tests/__init__.py app/services/tests/test_ai_module_telemetry.py
git commit -m "feat: record_ai_module_run accepts input_payload + output_payload kwargs

Returns the inserted row's id so callers can correlate the AgentRun
with downstream artifacts. Backwards-compatible: existing callers pass
nothing and persist NULL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Allocation engine persists `AgentRun`

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation_service.py:256-328`
- Create: `app/services/ai_bridge/tests/__init__.py` (empty)
- Create: `app/services/ai_bridge/tests/test_asset_allocation_persists_agent_run.py`

- [ ] **Step 1: Create the test directory marker**

```bash
mkdir -p app/services/ai_bridge/tests
touch app/services/ai_bridge/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `app/services/ai_bridge/tests/test_asset_allocation_persists_agent_run.py`:

```python
"""Verify compute_allocation_result writes a structured AgentRun row."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge import asset_allocation_service as svc


class _FakeUser:
    def __init__(self):
        self.date_of_birth = date(1986, 1, 1)


class AllocationPersistsAgentRunTests(unittest.TestCase):

    def test_agent_run_row_written_with_payloads(self):
        captured: dict = {}

        async def fake_record(db, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        # Build a minimal AllocationInput stub
        alloc_input = MagicMock()
        alloc_input.model_dump = MagicMock(return_value={"corpus": 8_000_000})
        alloc_input.effective_risk_score = 5.4
        alloc_input.risk_willingness = None
        alloc_input.risk_capacity_score = None
        alloc_input.age = 39
        alloc_input.total_corpus = 8_000_000
        alloc_input.goals = []

        # Build a minimal output stub
        output = MagicMock()
        output.grand_total = 8_000_000
        output.model_dump = MagicMock(return_value={"grand_total": 8_000_000})

        # Patch dependencies
        with patch.object(svc, "build_goal_allocation_input_for_user",
                          return_value=(alloc_input, {})), \
             patch.object(svc.asyncio, "to_thread",
                          new=AsyncMock(return_value=({"step7_output": {}}, output))), \
             patch.object(svc.get_settings, "__call__",
                          create=True), \
             patch.object(svc, "record_ai_module_run", side_effect=fake_record), \
             patch("app.services.ai_bridge.asset_allocation_service.get_settings") as gs:
            gs.return_value.get_anthropic_asset_allocation_key.return_value = "sk-fake"

            db = MagicMock()
            asyncio.run(svc.compute_allocation_result(
                _FakeUser(), "test question",
                db=db, persist_recommendation=False,
                acting_user_id=uuid.uuid4(), chat_session_id=uuid.uuid4(),
            ))

        self.assertEqual(captured.get("module"), "goal_based_allocation")
        self.assertIn("input_payload", captured)
        self.assertIn("output_payload", captured)
        self.assertEqual(captured["input_payload"], {"corpus": 8_000_000})
        self.assertIn("allocation_result", captured["output_payload"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_persists_agent_run.py -v
```
Expected: FAIL — `record_ai_module_run` is not yet imported/called from the service module.

- [ ] **Step 4: Implement — wire the persistence call**

Edit `app/services/ai_bridge/asset_allocation_service.py`. Add to imports near the top (after the SQLAlchemy import on line 17):

```python
from app.services.ai_module_telemetry import record_ai_module_run
```

Then in `compute_allocation_result`, after the existing block that persists the recommendation (currently ends at line 321 with `trace_line(f"persisted: rebalancing_id=...")`), insert a new block before the `return AllocationRunOutcome(...)` statement:

```python
    # Persist AgentRun row for follow-up reasoning. Does not replace
    # allocation_recommendation_persist; this captures structured I/O for chat.
    if db is not None and acting_user_id is not None and output is not None:
        try:
            await record_ai_module_run(
                db,
                user_id=acting_user_id,
                session_id=chat_session_id,
                module="goal_based_allocation",
                reason="full_pipeline_run",
                intent_detected=None,
                spine_mode=spine_mode,
                input_payload=alloc_input.model_dump(mode="json"),
                output_payload={
                    "allocation_result": output.model_dump(mode="json"),
                    "correlation_ids": {
                        "snapshot_id": str(snap_id) if snap_id else None,
                        "rebalancing_recommendation_id": str(reb_id) if reb_id else None,
                    },
                },
                emit_standard_log=False,
            )
        except Exception as exc:
            logger.warning("AgentRun persistence skipped (non-fatal): %s", exc)
```

The `try/except` makes persistence non-fatal — if it fails for any reason, the user still gets their allocation reply.

- [ ] **Step 5: Run the test — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_persists_agent_run.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`.

- [ ] **Step 7: Commit**

```bash
git add app/services/ai_bridge/asset_allocation_service.py app/services/ai_bridge/tests/__init__.py app/services/ai_bridge/tests/test_asset_allocation_persists_agent_run.py
git commit -m "feat: allocation engine persists structured AgentRun

After every successful pipeline run, write a chat_ai_module_runs row with
the AllocationInput as input_payload and AllocationResult + correlation
IDs as output_payload. Failure is logged and swallowed (non-fatal) so the
user always gets their allocation reply.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add `wants_fresh_recomputation` to classifier (model + LLM schema + prompt)

**Files:**
- Modify: `AI_Agents/src/intent_classifier/models.py:27-32`
- Modify: `AI_Agents/src/intent_classifier/classifier.py:17-25, 115-121`
- Modify: `AI_Agents/src/intent_classifier/prompts.py:140-149`
- Modify: `AI_Agents/tests/test_intent_classifier.py`

- [ ] **Step 1: Locate existing classifier tests**

```bash
head -30 AI_Agents/tests/test_intent_classifier.py
```
The file uses `unittest.TestCase` with mocks at `chain.invoke`. Match this style.

- [ ] **Step 2: Write the failing test**

Append a new test class to `AI_Agents/tests/test_intent_classifier.py`:

```python
class WantsFreshRecomputationFieldTests(unittest.TestCase):
    """The classifier returns a wants_fresh_recomputation flag."""

    def test_default_false_for_explanation_question(self):
        from intent_classifier import IntentClassifier, ClassificationInput
        clf = IntentClassifier(api_key="sk-fake")
        clf.chain = MagicMock()
        clf.chain.invoke.return_value = _FakeLLMOut(
            intent="portfolio_optimisation", confidence=0.9,
            is_follow_up=True, reasoning="explanation",
            wants_fresh_recomputation=False,
        )

        result = clf.classify(ClassificationInput(
            customer_question="is this too aggressive?",
        ))
        self.assertFalse(result.wants_fresh_recomputation)

    def test_true_when_user_asks_for_redo(self):
        from intent_classifier import IntentClassifier, ClassificationInput
        clf = IntentClassifier(api_key="sk-fake")
        clf.chain = MagicMock()
        clf.chain.invoke.return_value = _FakeLLMOut(
            intent="portfolio_optimisation", confidence=0.9,
            is_follow_up=True, reasoning="redo with new money",
            wants_fresh_recomputation=True,
        )

        result = clf.classify(ClassificationInput(
            customer_question="actually I have 10L more, redo this",
        ))
        self.assertTrue(result.wants_fresh_recomputation)
```

Add this helper near the top of the same test file (after existing imports):

```python
class _FakeLLMOut:
    """Stand-in for the LangChain-structured LLM output."""
    def __init__(self, *, intent, confidence, is_follow_up, reasoning,
                 wants_fresh_recomputation=False):
        self.intent = intent
        self.confidence = confidence
        self.is_follow_up = is_follow_up
        self.reasoning = reasoning
        self.wants_fresh_recomputation = wants_fresh_recomputation
```

If `MagicMock` isn't already imported, add `from unittest.mock import MagicMock`.

- [ ] **Step 3: Run the test — expect failure**

```bash
python3 -m pytest AI_Agents/tests/test_intent_classifier.py -v -k WantsFresh
```
Expected: FAIL — `ClassificationResult` and/or `_LLMOutput` does not have `wants_fresh_recomputation`.

- [ ] **Step 4: Add field to `ClassificationResult`**

Edit `AI_Agents/src/intent_classifier/models.py` — extend `ClassificationResult` (around line 27):

```python
class ClassificationResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    is_follow_up: bool = False
    wants_fresh_recomputation: bool = False
    reasoning: str
    out_of_scope_message: Optional[str] = None
```

- [ ] **Step 5: Add field to LLM-output schema and pass through `classify`**

Edit `AI_Agents/src/intent_classifier/classifier.py`. In `_LLMOutput` (around line 17), add:

```python
class _LLMOutput(BaseModel):
    """Structured output schema returned by the LLM."""
    intent: str = Field(description="The classified intent category.")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0.")
    is_follow_up: bool = Field(
        default=False,
        description="True if the message continues the previous conversation topic; false if it starts a new topic.",
    )
    wants_fresh_recomputation: bool = Field(
        default=False,
        description="True only when the customer is explicitly asking the agent to recompute with new inputs (new money, new constraints, redo). False for explanation/critique/'what if' questions.",
    )
    reasoning: str = Field(description="One or two sentences explaining why this intent was chosen.")
```

Then update the `classify` return statement (around line 115) to forward the new field:

```python
        return ClassificationResult(
            intent=intent,
            confidence=raw.confidence,
            is_follow_up=raw.is_follow_up,
            wants_fresh_recomputation=raw.wants_fresh_recomputation,
            reasoning=raw.reasoning,
            out_of_scope_message=_canned_responses.get(intent),
        )
```

- [ ] **Step 6: Add prompt rules for the new field**

Edit `AI_Agents/src/intent_classifier/prompts.py`. Insert before the closing `"""` of `SYSTEM_PROMPT` (after the `Classification Rules` section):

```python
---

## Recomputation Detection

Set `wants_fresh_recomputation = true` ONLY when the customer is explicitly
asking the agent to recompute with new inputs:
- Adds new constraints ("redo this without arbitrage", "redo without my
  emergency fund")
- Adds new money or new goals ("I have 10L more, redo", "now also plan
  for child's education")
- Asks for re-execution ("rerun", "redo", "recompute", "let's do this again")

Set `wants_fresh_recomputation = false` for:
- Explanation, critique, or "why" questions ("is this too aggressive?",
  "why so much arbitrage?", "what does flexi-cap mean here?")
- Counterfactual exploration ("what if my risk score were 7?") — these are
  hypothetical, not requests to change the saved plan
- Mutation requests ("swap arbitrage for liquid") — these are not recomputation;
  the followup handler decides how to respond
- Any first-turn question with no prior conversation
```

- [ ] **Step 7: Run the test — expect pass**

```bash
python3 -m pytest AI_Agents/tests/test_intent_classifier.py -v -k WantsFresh
```
Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add AI_Agents/src/intent_classifier/models.py AI_Agents/src/intent_classifier/classifier.py AI_Agents/src/intent_classifier/prompts.py AI_Agents/tests/test_intent_classifier.py
git commit -m "feat: classifier emits wants_fresh_recomputation signal

True only on explicit recompute requests (new money, new constraints,
redo). Used downstream by ChatBrain to decide whether a follow-up should
narrate the existing AgentRun or re-run the agent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Pass `active_intent` through the classifier service

**Files:**
- Modify: `app/services/ai_bridge/intent_classifier_service.py:124-139`
- Create: `app/services/ai_bridge/tests/test_classifier_service_active_intent.py`

- [ ] **Step 1: Write the failing test**

Create `app/services/ai_bridge/tests/test_classifier_service_active_intent.py`:

```python
"""classify_user_message forwards active_intent to ClassificationInput."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.services.ai_bridge.intent_classifier_service import classify_user_message


class ActiveIntentForwardingTests(unittest.TestCase):

    def test_active_intent_forwarded_to_classification_input(self):
        captured = {}

        def fake_classify(self, inp):
            captured["active_intent"] = inp.active_intent
            return MagicMock(intent=MagicMock(value="portfolio_optimisation"),
                              confidence=0.9, is_follow_up=True,
                              wants_fresh_recomputation=False, reasoning="...",
                              out_of_scope_message=None)

        with patch("app.services.ai_bridge.intent_classifier_service._get_classifier") as gc, \
             patch.object(asyncio, "to_thread",
                          new=lambda fn, *a, **kw: _wrap_sync(fn, *a, **kw)):
            classifier = MagicMock()
            classifier.classify = lambda inp: fake_classify(classifier, inp)
            gc.return_value = classifier

            asyncio.run(classify_user_message(
                customer_question="is this too aggressive?",
                conversation_history=[],
                active_intent="portfolio_optimisation",
            ))

        # active_intent travels to ClassificationInput.active_intent
        self.assertIsNotNone(captured["active_intent"])
        self.assertEqual(captured["active_intent"].value, "portfolio_optimisation")


async def _wrap_sync(fn, *args, **kwargs):
    return fn(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_classifier_service_active_intent.py -v
```
Expected: FAIL — `classify_user_message` does not accept `active_intent` kwarg.

- [ ] **Step 3: Implement — extend the bridge function**

Edit `app/services/ai_bridge/intent_classifier_service.py`. Replace `classify_user_message` (lines 124–139):

```python
async def classify_user_message(
    customer_question: str,
    conversation_history: list[dict[str, str]] | None = None,
    active_intent: str | None = None,
) -> ClassificationResult:
    """Classify intent via Anthropic; falls back to OpenAI on failure."""
    history = [
        ConversationMessage(role=m["role"], content=m["content"])
        for m in (conversation_history or [])
    ]
    active = Intent(active_intent) if active_intent else None
    try:
        inp = ClassificationInput(
            customer_question=customer_question,
            conversation_history=history,
            active_intent=active,
        )
        return await asyncio.to_thread(_get_classifier().classify, inp)
    except Exception as exc:
        logger.warning("Anthropic classifier failed (%s), trying OpenAI fallback...", exc)

    return await _classify_via_openai(customer_question, conversation_history)
```

(The OpenAI fallback path doesn't yet thread active_intent through — acceptable for now since OpenAI is only the recovery path; we'll address if it becomes a real signal.)

- [ ] **Step 4: Run the test — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_classifier_service_active_intent.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/intent_classifier_service.py app/services/ai_bridge/tests/test_classifier_service_active_intent.py
git commit -m "feat: classify_user_message accepts active_intent kwarg

Forwarded to ClassificationInput.active_intent so the classifier prompt
can anchor follow-up resolution to the prior turn's intent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `TurnContext` type + builder

**Files:**
- Create: `app/services/chat_core/turn_context.py`
- Create: `app/services/chat_core/tests/__init__.py` (empty)
- Create: `app/services/chat_core/tests/test_turn_context.py`

- [ ] **Step 1: Create the test directory marker**

```bash
mkdir -p app/services/chat_core/tests
touch app/services/chat_core/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `app/services/chat_core/tests/test_turn_context.py`:

```python
"""TurnContext builder tests."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.services.chat_core.turn_context import (
    AgentRunRecord, build_turn_context,
)


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class TurnContextBuilderTests(unittest.TestCase):

    def test_loads_last_agent_run_per_module_and_active_intent(self):
        sid = uuid.uuid4()

        # Stub DB: first execute() returns last-AgentRun-per-module rows;
        # second execute() returns the most-recent intent row.
        alloc_row = MagicMock(
            id=uuid.uuid4(),
            module="goal_based_allocation",
            intent_detected="portfolio_optimisation",
            input_payload={"corpus": 8_000_000},
            output_payload={"allocation_result": {"grand_total": 8_000_000}},
            created_at=datetime(2026, 4, 27, 9, 0),
        )
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            _StubResult([alloc_row]),                        # last AgentRun per module
            _StubResult([("portfolio_optimisation",)]),       # last intent_detected
        ])

        # Minimal turn-like object
        turn = MagicMock(
            user_ctx=MagicMock(),
            user_question="is this too aggressive?",
            conversation_history=[],
            client_context=None,
            session_id=sid,
            db=db,
            user_id=uuid.uuid4(),
            effective_user_id=uuid.uuid4(),
        )

        ctx = asyncio.run(build_turn_context(turn))

        self.assertIn("goal_based_allocation", ctx.last_agent_runs)
        rec: AgentRunRecord = ctx.last_agent_runs["goal_based_allocation"]
        self.assertEqual(rec.module, "goal_based_allocation")
        self.assertEqual(rec.input_payload, {"corpus": 8_000_000})
        self.assertEqual(ctx.active_intent, "portfolio_optimisation")

    def test_empty_session_returns_empty_runs(self):
        sid = uuid.uuid4()
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            _StubResult([]),
            _StubResult([]),
        ])
        turn = MagicMock(
            user_ctx=MagicMock(),
            user_question="hello",
            conversation_history=[],
            client_context=None,
            session_id=sid,
            db=db,
            user_id=uuid.uuid4(),
            effective_user_id=uuid.uuid4(),
        )

        ctx = asyncio.run(build_turn_context(turn))

        self.assertEqual(ctx.last_agent_runs, {})
        self.assertIsNone(ctx.active_intent)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test — expect failure**

```bash
python3 -m pytest app/services/chat_core/tests/test_turn_context.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 4: Implement — create `turn_context.py`**

Create `app/services/chat_core/turn_context.py`:

```python
"""Per-turn context bundle: history + last AgentRun per module + active intent.

Built once per chat turn from ``ChatTurnInput``. Consumed by ChatBrain
routing and downstream handlers (e.g. asset_allocation_followup).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_ai_module_run import ChatAiModuleRun
from app.models.user import User
from app.services.chat_core.types import ChatTurnInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunRecord:
    """Frozen view of one persisted chat_ai_module_runs row used by handlers."""
    id: uuid.UUID
    module: str
    intent_detected: str | None
    input_payload: dict[str, Any] | None
    output_payload: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True)
class TurnContext:
    """Everything a handler needs about the current turn + session history."""
    user_ctx: User
    user_question: str
    conversation_history: list[dict[str, str]]
    client_context: dict[str, Any] | None
    session_id: uuid.UUID
    db: AsyncSession | None
    effective_user_id: uuid.UUID
    last_agent_runs: dict[str, AgentRunRecord]
    active_intent: str | None


async def build_turn_context(turn: ChatTurnInput) -> TurnContext:
    """Load last AgentRun per module + last intent_detected for this session."""
    last_runs: dict[str, AgentRunRecord] = {}
    active_intent: str | None = None

    if turn.db is not None and turn.session_id is not None:
        try:
            last_runs = await _load_last_agent_runs(turn.db, turn.session_id)
            active_intent = await _load_active_intent(turn.db, turn.session_id)
        except Exception as exc:
            # Non-fatal: degrade to empty context so the chat turn still works.
            logger.warning("build_turn_context degraded (%s); using empty context", exc)

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
    )


async def _load_last_agent_runs(
    db: AsyncSession, session_id: uuid.UUID,
) -> dict[str, AgentRunRecord]:
    """One row per module, the most recent with output_payload populated."""
    stmt = text(
        "SELECT DISTINCT ON (module) id, module, intent_detected, "
        "input_payload, output_payload, created_at "
        "FROM chat_ai_module_runs "
        "WHERE session_id = :sid AND output_payload IS NOT NULL "
        "ORDER BY module, created_at DESC"
    )
    result = await db.execute(stmt, {"sid": session_id})
    rows = result.all()
    return {
        r.module: AgentRunRecord(
            id=r.id,
            module=r.module,
            intent_detected=r.intent_detected,
            input_payload=r.input_payload,
            output_payload=r.output_payload,
            created_at=r.created_at,
        )
        for r in rows
    }


async def _load_active_intent(
    db: AsyncSession, session_id: uuid.UUID,
) -> str | None:
    """Most-recent intent_detected for this session, regardless of module."""
    stmt = (
        select(ChatAiModuleRun.intent_detected)
        .where(ChatAiModuleRun.session_id == session_id)
        .where(ChatAiModuleRun.intent_detected.isnot(None))
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    return row
```

- [ ] **Step 5: Run the test — expect pass**

```bash
python3 -m pytest app/services/chat_core/tests/test_turn_context.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/chat_core/turn_context.py app/services/chat_core/tests/__init__.py app/services/chat_core/tests/test_turn_context.py
git commit -m "feat: TurnContext bundles per-turn history + last AgentRuns + active_intent

build_turn_context loads one row per module (most recent AgentRun in
session) and the most recent intent_detected from chat_ai_module_runs.
Failures degrade to empty context so chat continues to work.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Wire `TurnContext` + active_intent into the brain (no routing change yet)

**Files:**
- Modify: `app/services/chat_core/brain.py` (imports + `run_turn` body)

- [ ] **Step 1: Wire build_turn_context and pass active_intent**

Edit `app/services/chat_core/brain.py`. Add an import near the top:

```python
from app.services.chat_core.turn_context import build_turn_context, TurnContext
```

In `run_turn` (around line 117, just before the `try:` that starts intent classification), insert:

```python
            # --- Step 0: per-turn context bundle (history + last AgentRun per module) ---
            turn_context: TurnContext = await build_turn_context(turn)
            trace_line(
                f"turn_context: last_runs={list(turn_context.last_agent_runs.keys())} "
                f"active_intent={turn_context.active_intent}"
            )
```

Then update the `classify_user_message` call (around line 118) to pass `active_intent`:

```python
            classification = await classify_user_message(
                customer_question=turn.user_question,
                conversation_history=turn.conversation_history,
                active_intent=turn_context.active_intent,
            )
```

This task does NOT change routing — `turn_context` is built and `active_intent` is forwarded, but no behavior changes yet. The bug is still present until Task 9.

- [ ] **Step 2: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`.

- [ ] **Step 3: Run all collected unit tests — confirm no regression**

```bash
python3 -m pytest app/services/ai_bridge/tests/ app/services/chat_core/tests/ app/services/tests/ -v
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/services/chat_core/brain.py
git commit -m "feat: brain builds TurnContext per turn and passes active_intent to classifier

No routing change yet — context is computed and logged. Sets up the
Task-9 routing change without touching dispatch behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: `followup_dispatcher`

**Files:**
- Create: `app/services/ai_bridge/followup_dispatcher.py`
- Create: `app/services/ai_bridge/tests/test_followup_dispatcher.py`

- [ ] **Step 1: Write the failing test**

Create `app/services/ai_bridge/tests/test_followup_dispatcher.py`:

```python
"""followup_dispatcher: registry + dispatch behavior."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from unittest.mock import MagicMock

from app.services.ai_bridge import followup_dispatcher as fd
from app.services.chat_core.turn_context import AgentRunRecord


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="goal_based_allocation",
        intent_detected="portfolio_optimisation",
        input_payload={},
        output_payload={"allocation_result": {}},
        created_at=datetime.utcnow(),
    )


class FollowupDispatcherTests(unittest.TestCase):

    def setUp(self):
        # Reset registry between tests
        fd._HANDLERS.clear()

    def test_register_and_dispatch_calls_handler(self):
        called = {}

        @fd.register("portfolio_optimisation")
        async def fake_handler(agent_run, ctx):
            called["agent_run"] = agent_run
            called["ctx"] = ctx
            return "narrated text"

        ctx = MagicMock()
        result = asyncio.run(fd.dispatch_followup(
            "portfolio_optimisation", _agent_run(), ctx,
        ))
        self.assertEqual(result, "narrated text")
        self.assertIs(called["ctx"], ctx)

    def test_unregistered_intent_raises(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(fd.dispatch_followup(
                "no_such_intent", _agent_run(), MagicMock(),
            ))

    def test_register_multiple_intents_for_one_handler(self):
        @fd.register("portfolio_optimisation")
        @fd.register("goal_planning")
        async def shared(agent_run, ctx):
            return "shared response"

        for intent in ("portfolio_optimisation", "goal_planning"):
            self.assertEqual(
                asyncio.run(fd.dispatch_followup(intent, _agent_run(), MagicMock())),
                "shared response",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_followup_dispatcher.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement — create the dispatcher**

Create `app/services/ai_bridge/followup_dispatcher.py`:

```python
"""Per-intent followup handler registry + dispatcher.

Handlers register themselves via the @register(intent) decorator at import
time. ChatBrain calls dispatch_followup() when a turn is identified as a
follow-up that should narrate a prior AgentRun rather than re-run the agent.
"""

from __future__ import annotations

from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


Handler = Callable[
    ["AgentRunRecord", "TurnContext"], Awaitable[str],
]

_HANDLERS: dict[str, Handler] = {}


def register(intent: str) -> Callable[[Handler], Handler]:
    """Register a followup handler for the given intent. Stackable."""
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[intent] = fn
        return fn
    return decorator


async def dispatch_followup(
    intent: str,
    agent_run: "AgentRunRecord",
    turn_context: "TurnContext",
) -> str:
    """Look up the handler for ``intent`` and invoke it."""
    handler = _HANDLERS.get(intent)
    if handler is None:
        raise RuntimeError(
            f"No followup handler registered for intent={intent!r}"
        )
    return await handler(agent_run, turn_context)
```

- [ ] **Step 4: Run the test — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_followup_dispatcher.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/followup_dispatcher.py app/services/ai_bridge/tests/test_followup_dispatcher.py
git commit -m "feat: followup_dispatcher registry for per-intent followup handlers

Modules register themselves at import time via @register(intent). ChatBrain
calls dispatch_followup() with the intent + last AgentRun + TurnContext;
the dispatcher invokes the matching handler.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `asset_allocation_followup` — `detect_action` + narrate path

**Files:**
- Create: `app/services/ai_bridge/asset_allocation_followup.py`
- Create: `app/services/ai_bridge/tests/test_asset_allocation_followup.py`

This task implements the bridge module, the action-detection LLM call, and the narrate path. Counterfactual + redirect paths land in Task 10 + 11.

- [ ] **Step 1: Write the failing test (narrate path)**

Create `app/services/ai_bridge/tests/test_asset_allocation_followup.py`:

```python
"""asset_allocation_followup: handler narrates persisted snapshots."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge import asset_allocation_followup as mod
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="goal_based_allocation",
        intent_detected="portfolio_optimisation",
        input_payload={"effective_risk_score": 5.4, "age": 39, "total_corpus": 8_000_000},
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
        },
        created_at=datetime.utcnow(),
    )


def _ctx(question: str) -> TurnContext:
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=None, first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="portfolio_optimisation",
    )


class NarratePathTests(unittest.TestCase):

    def test_narrate_path_returns_llm_text(self):
        # Stub detect_action → narrate
        action = mod.FollowupAction(mode="narrate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_narrate_with_llm",
                          new=AsyncMock(return_value="narrated answer")):
            text = asyncio.run(mod.handle_allocation_followup(
                _agent_run(), _ctx("is this too aggressive?"),
            ))
        self.assertEqual(text, "narrated answer")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_followup.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement — narrate path scaffold**

Create `app/services/ai_bridge/asset_allocation_followup.py`:

```python
"""Read-only narration + counterfactual + redirect handler for allocation followups.

Registered against ``portfolio_optimisation`` and ``goal_planning`` intents.
The brain invokes ``handle_allocation_followup`` whenever a follow-up turn
should reason over the persisted allocation snapshot rather than re-running
the engine.
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
from app.services.ai_bridge.followup_dispatcher import register
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action-detection schema (returned by the small classifier LLM call)
# ---------------------------------------------------------------------------

class FollowupAction(BaseModel):
    mode: Literal["narrate", "counterfactual", "redirect_mutation"]
    counterfactual_overrides: Optional[dict[str, Any]] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)


_DETECT_SYSTEM = """You decide how to handle a follow-up question about a
previously-shown asset allocation. Return one of three modes:

- "narrate" — the customer is asking for explanation, critique, or
  clarification of the existing plan ("is this too aggressive?",
  "why so much arbitrage?", "what does flexi-cap mean?").
- "counterfactual" — the customer is asking a hypothetical "what if"
  about a single overrideable input. The ONLY supported override in
  this iteration is `effective_risk_score` (1.0–10.0). Set
  `counterfactual_overrides = {"effective_risk_score": <value>}`. Any
  other override request must fall through to "redirect_mutation".
- "redirect_mutation" — the customer wants to change holdings, swap
  funds, or update saved profile data ("swap arbitrage for liquid",
  "exclude my emergency fund"). Set `redirect_reason` to a short
  description of what they want. The handler will respond with a
  templated redirect to the Profile UI.
"""


# ---------------------------------------------------------------------------
# Narration LLM
# ---------------------------------------------------------------------------

_NARRATE_SYSTEM = """You are Prozper's allocation explainer. You answer
follow-up questions about a customer's already-shown goal-based allocation
plan. Use the provided snapshot to answer. Be concise (4-8 sentences),
specific (cite numbers from the snapshot), and warm. Never invent funds
or numbers. If the question can't be answered from the snapshot, say so
and offer next steps."""


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

@register("portfolio_optimisation")
@register("goal_planning")
async def handle_allocation_followup(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> str:
    """Decide narrate / counterfactual / redirect, then dispatch."""
    action = await _detect_action(agent_run, ctx)
    logger.info("allocation_followup mode=%s overrides=%s",
                action.mode, action.counterfactual_overrides)

    if action.mode == "narrate":
        return await _narrate_with_llm(agent_run, ctx)

    if action.mode == "counterfactual":
        # Implemented in Task 10
        from app.services.ai_bridge.asset_allocation_followup_counterfactual import (
            run_counterfactual,
        )
        return await run_counterfactual(agent_run, ctx, action.counterfactual_overrides or {})

    # redirect_mutation (default branch)
    return _format_redirect(action.redirect_reason or "change your plan")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

async def _detect_action(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> FollowupAction:
    """One Haiku call returning a FollowupAction."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=256,
    ).with_structured_output(FollowupAction)

    snapshot = json.dumps(agent_run.output_payload, default=str)[:6000]
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Most recent allocation snapshot (truncated):\n{snapshot}"
    )

    return await _ainvoke(llm, _DETECT_SYSTEM, user_block)


async def _narrate_with_llm(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> str:
    """Generate the narrative reply from the persisted snapshot."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=600,
    )

    snapshot = json.dumps(agent_run.output_payload, default=str)
    profile = {
        "effective_risk_score": (agent_run.input_payload or {}).get("effective_risk_score"),
        "age": (agent_run.input_payload or {}).get("age"),
        "total_corpus": (agent_run.input_payload or {}).get("total_corpus"),
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

    msg = await _ainvoke_text(llm, _NARRATE_SYSTEM, user_block)
    return msg


def _format_redirect(reason: str) -> str:
    return (
        f"To {reason}, head to your **Profile** section and update the "
        "relevant inputs — I'll regenerate your plan automatically. If "
        "you want, just describe what you'd like differently and I'll "
        "re-run the allocation."
    )


# ---------------------------------------------------------------------------
# Async LangChain helpers (small wrappers so tests can patch)
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

The counterfactual import lives in `asset_allocation_followup_counterfactual.py` (Task 10) — until then it's an unbound import that only triggers if action.mode == "counterfactual" is reached. The narrate path (this task's scope) does not exercise it.

- [ ] **Step 4: Run the test — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_followup.py::NarratePathTests -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/asset_allocation_followup.py app/services/ai_bridge/tests/test_asset_allocation_followup.py
git commit -m "feat: asset_allocation_followup handler with narrate path

Adds the action-detection LLM (FollowupAction: narrate / counterfactual /
redirect_mutation) and the narrate path that reads the persisted snapshot
and produces a Haiku-generated explanatory reply. Counterfactual lives in
a sibling module added in the next commit; redirect uses a templated
response.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `asset_allocation_followup` — counterfactual path (risk-score override)

**Files:**
- Create: `app/services/ai_bridge/asset_allocation_followup_counterfactual.py`
- Modify: `app/services/ai_bridge/tests/test_asset_allocation_followup.py` (append test class)

- [ ] **Step 1: Append the failing test**

Add to `app/services/ai_bridge/tests/test_asset_allocation_followup.py`:

```python
class CounterfactualPathTests(unittest.TestCase):

    def test_counterfactual_runs_engine_with_override_no_persistence(self):
        from app.services.ai_bridge import asset_allocation_followup_counterfactual as cf

        captured = {}

        async def fake_compute(user, question, *, db, persist_recommendation,
                                acting_user_id, chat_session_id, spine_mode):
            captured["persist"] = persist_recommendation
            outcome = MagicMock()
            outcome.result = MagicMock()
            outcome.result.grand_total = 8_000_000
            outcome.result.model_dump = MagicMock(return_value={"grand_total": 8_000_000})
            outcome.blocking_message = None
            return outcome

        agent_run = _agent_run()
        # AllocationInput-shaped fields needed for override path
        agent_run = AgentRunRecord(
            id=agent_run.id,
            module=agent_run.module,
            intent_detected=agent_run.intent_detected,
            input_payload={
                "effective_risk_score": 5.4, "age": 39, "annual_income": 1_000_000,
                "osi": 0.3, "savings_rate_adjustment": "none", "gap_exceeds_3": False,
                "total_corpus": 8_000_000, "monthly_household_expense": 50_000,
                "tax_regime": "new", "effective_tax_rate": 30.0, "goals": [],
            },
            output_payload=agent_run.output_payload,
            created_at=agent_run.created_at,
        )

        with patch.object(cf, "compute_allocation_result", new=fake_compute), \
             patch.object(cf, "_narrate_counterfactual",
                          new=AsyncMock(return_value="hypothetical text")):
            text = asyncio.run(cf.run_counterfactual(
                agent_run, _ctx("what if my risk were 7?"),
                {"effective_risk_score": 7.0},
            ))

        self.assertEqual(text, "hypothetical text")
        self.assertFalse(captured["persist"])  # never persists

    def test_invalid_override_falls_through_to_redirect(self):
        from app.services.ai_bridge import asset_allocation_followup_counterfactual as cf

        agent_run = _agent_run()
        text = asyncio.run(cf.run_counterfactual(
            agent_run, _ctx("what if my goal amount were higher?"),
            {"goal_amount": 50_000_000},  # not in allow-list
        ))
        self.assertIn("Profile", text)
```

- [ ] **Step 2: Run the test — expect failure**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_followup.py::CounterfactualPathTests -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement — counterfactual sibling module**

Create `app/services/ai_bridge/asset_allocation_followup_counterfactual.py`:

```python
"""Counterfactual ('what if?') path for allocation followups.

Allowed overrides (this iteration): ``effective_risk_score`` only.
Anything else falls through to the redirect template.

Counterfactual results are NEVER persisted as AgentRuns or recommendation
rows — they are exploratory hypotheticals, not the user's saved plan.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.services.ai_bridge.asset_allocation_service import compute_allocation_result
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)

_ALLOWED_OVERRIDE_KEYS = {"effective_risk_score"}

_REDIRECT_TEMPLATE = (
    "I can only run 'what if' on a small set of inputs from chat right now "
    "(your risk score). For other changes, head to your **Profile** section "
    "and update the relevant inputs — I'll regenerate your plan automatically."
)

_NARRATE_SYSTEM = """You explain the result of a hypothetical allocation
calculation. Make the hypothetical-ness explicit ('this is hypothetical, not
your saved plan'). Compare to the existing plan briefly. Be concise (4-7
sentences). Cite specific numbers."""


async def run_counterfactual(
    agent_run: AgentRunRecord,
    ctx: TurnContext,
    overrides: dict[str, Any],
) -> str:
    """Apply overrides to the original AllocationInput, run the engine, narrate."""
    illegal = set(overrides.keys()) - _ALLOWED_OVERRIDE_KEYS
    if illegal or not overrides:
        return _REDIRECT_TEMPLATE

    if agent_run.input_payload is None:
        return _REDIRECT_TEMPLATE

    # Compute against the user's current state but with risk_score override
    # applied. Note: compute_allocation_result rebuilds the AllocationInput
    # from User; for this iteration we override on the user object itself
    # via a transient attribute. (Future iteration: thread overrides through
    # build_goal_allocation_input_for_user.)
    risk_override = overrides.get("effective_risk_score")
    user = ctx.user_ctx
    if risk_override is not None:
        # The builder reads from user.risk_profile.effective_risk_score.
        # Set a transient attribute the builder recognises (Task 11
        # extends the builder to honour this).
        setattr(user, "_chat_risk_score_override", float(risk_override))

    try:
        outcome = await compute_allocation_result(
            user, ctx.user_question,
            db=None,                          # no DB writes
            persist_recommendation=False,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
            spine_mode="counterfactual",
        )
    finally:
        if hasattr(user, "_chat_risk_score_override"):
            delattr(user, "_chat_risk_score_override")

    if outcome.blocking_message:
        return outcome.blocking_message
    if outcome.result is None:
        return (
            "I couldn't compute that hypothetical right now. Try again "
            "or update your inputs in your Profile."
        )

    return await _narrate_counterfactual(agent_run, ctx, outcome.result, overrides)


async def _narrate_counterfactual(
    agent_run: AgentRunRecord,
    ctx: TurnContext,
    new_result: Any,
    overrides: dict[str, Any],
) -> str:
    """Narrate the hypothetical result side-by-side with the saved plan."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=500,
    )

    saved = (agent_run.output_payload or {}).get("allocation_result", {})
    new = new_result.model_dump(mode="json") if hasattr(new_result, "model_dump") else new_result

    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Overrides applied (hypothetical): {json.dumps(overrides)}\n\n"
        f"Saved plan (do NOT change this): {json.dumps(saved, default=str)}\n\n"
        f"Hypothetical result: {json.dumps(new, default=str)}\n\n"
        "Narrate the hypothetical, comparing to the saved plan. Make it "
        "clear the hypothetical is not the user's saved plan."
    )
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": _NARRATE_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_block),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    return raw.content if hasattr(raw, "content") else str(raw)
```

- [ ] **Step 4: Extend `build_goal_allocation_input_for_user` to honour the override**

The counterfactual relies on the input builder honoring `_chat_risk_score_override`. Find and edit `app/services/ai_bridge/goal_allocation_input_builder.py` — locate where `effective_risk_score` is computed and read into `AllocationInput`. Add immediately before that assignment:

```python
        # Counterfactual override path: chat-only, transient attribute.
        override = getattr(user, "_chat_risk_score_override", None)
        if override is not None:
            effective_risk_score = float(override)
```

(Use the actual local-variable name in that file. If the variable doesn't exist as a local, add a small targeted patch wrapping the existing computation: e.g., `effective_risk_score = float(getattr(user, "_chat_risk_score_override", effective_risk_score))`.)

- [ ] **Step 5: Run the test — expect pass**

```bash
python3 -m pytest app/services/ai_bridge/tests/test_asset_allocation_followup.py::CounterfactualPathTests -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_bridge/asset_allocation_followup_counterfactual.py app/services/ai_bridge/goal_allocation_input_builder.py app/services/ai_bridge/tests/test_asset_allocation_followup.py
git commit -m "feat: counterfactual path for allocation followups (risk_score override only)

Runs the engine with effective_risk_score overridden via a transient
attribute on the user object. Never persists. Anything outside the
allow-list (effective_risk_score) falls through to the redirect template.
A future iteration extends overrides to goals/contributions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Brain routing — dispatch follow-ups to the dispatcher

**Files:**
- Modify: `app/services/chat_core/brain.py` (`run_turn`, portfolio_optimisation branch)

- [ ] **Step 1: Add the routing change**

Edit `app/services/chat_core/brain.py`. Locate the `if intent_value in ("portfolio_optimisation", "goal_planning"):` branch (currently around line 138). Replace it with:

```python
            if intent_value in ("portfolio_optimisation", "goal_planning"):
                last_alloc = turn_context.last_agent_runs.get("goal_based_allocation")
                is_followup_route = (
                    classification.is_follow_up
                    and last_alloc is not None
                    and not classification.wants_fresh_recomputation
                )
                if is_followup_route:
                    flow.append(
                        "follow-up route → dispatch_followup (no engine run)"
                    )
                    trace_line(
                        "next module: followup_dispatcher → asset_allocation_followup"
                    )
                    # Import locally to register the handler at first use
                    # (the @register decorator runs at import time).
                    from app.services.ai_bridge import asset_allocation_followup  # noqa: F401
                    from app.services.ai_bridge.followup_dispatcher import dispatch_followup
                    text = await dispatch_followup(
                        intent_value, last_alloc, turn_context,
                    )
                    return await finalize(text)

                trace_line(
                    "next module: portfolio-style spine → "
                    "ailax_flow.detect_spine_mode / goal_based_allocation_pydantic"
                )
                p_content, p_reb, p_snap = await self._answer_portfolio_style(turn, flow)
                return await finalize(
                    p_content,
                    ideal_allocation_rebalancing_id=p_reb,
                    ideal_allocation_snapshot_id=p_snap,
                )
```

(The rest of the function body is unchanged.)

- [ ] **Step 2: Verify uvicorn hot-reloaded cleanly**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```
Expected: `200`.

- [ ] **Step 3: Run all unit tests — confirm no regression**

```bash
python3 -m pytest app/services/ -v
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/services/chat_core/brain.py
git commit -m "feat: brain routes follow-ups to dispatch_followup (bug fix)

When classifier says is_follow_up + a last AgentRun exists for goal_based_allocation
+ wants_fresh_recomputation is false, the brain calls dispatch_followup
instead of re-running the allocation engine. This is the commit that
fixes the original bug.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: End-to-end smoke verification

This task runs against the live uvicorn (`localhost:8000`) — manual verification that the bug is actually fixed end-to-end.

**Prereqs:**
- The running uvicorn (PID seen via `lsof -nP -iTCP:8000 -sTCP:LISTEN`) has hot-reloaded all prior commits.
- A test user exists (use whichever one you've been chatting with during dev).
- Postgres connection works (`alembic current` returns the new revision).

- [ ] **Step 1: Find the test user and a fresh chat session ID**

Use whichever script you typically use to seed/identify a dev user. If none, hit `/api/v1/chat/sessions` to create a new session. Record `user_id` and `session_id`.

- [ ] **Step 2: Run Turn 1 — request an allocation**

POST to the chat-send endpoint (path varies by your deployment; typical: `/api/v1/chat/sessions/{session_id}/messages`). Body:
```json
{"content": "Help me plan for retirement"}
```
Verify:
- Response is a full allocation brief.
- A `chat_ai_module_runs` row exists for this session with `module='goal_based_allocation'` and non-NULL `output_payload`:
```sql
SELECT id, module, intent_detected, output_payload IS NOT NULL AS has_output
FROM chat_ai_module_runs
WHERE session_id = '<sid>' ORDER BY created_at DESC;
```

- [ ] **Step 3: Run Turn 2 — narration follow-up**

POST to the same session:
```json
{"content": "Is this too aggressive?"}
```
Verify:
- Response is a narrative explanation **comparing the existing plan to risk benchmarks** — it should NOT be a freshly-generated allocation deck.
- Trace logs (`AILAX_AI_MODULE_RUN` lines) show `next module: followup_dispatcher → asset_allocation_followup`.
- No new `chat_ai_module_runs` row for `module='goal_based_allocation'` — the previous one is still the most recent.

- [ ] **Step 4: Run Turn 3 — counterfactual**

POST:
```json
{"content": "What if my risk score were 7?"}
```
Verify:
- Response narrates a hypothetical with explicit "this is hypothetical, not your saved plan" framing.
- A new `chat_ai_module_runs` row for `module='goal_based_allocation'` is **NOT** written — counterfactuals don't persist.

- [ ] **Step 5: Run Turn 4 — explicit recompute**

POST:
```json
{"content": "Actually, I have 10 lakh more to invest. Redo this."}
```
Verify:
- Response is a fresh full allocation deck (engine re-ran).
- A new `chat_ai_module_runs` row for `module='goal_based_allocation'` IS written; it's now the most-recent.

- [ ] **Step 6: Run Turn 5 — mutation redirect**

POST:
```json
{"content": "Swap arbitrage for liquid funds"}
```
Verify:
- Response is the templated redirect ("head to your Profile section…").
- No engine run, no new AgentRun row.

- [ ] **Step 7: Final commit (if any docs needed)**

If the smoke test passes cleanly and you've made any local doc tweaks (CLAUDE.md updates per project memory — local-only, not committed), no action needed.

If you discovered a real issue during smoke, return to the relevant task, fix, retest, and amend.

Smoke is informational, not commit-bound. The bug-fix commit is Task 11's commit.

---

## Self-review checklist

Before declaring done:

- [ ] Spec coverage: Tasks 1–11 cover every component in the spec (schema, persistence helper, allocation persistence, classifier extension, classifier service, TurnContext, brain wiring, dispatcher, allocation followup narrate + counterfactual + redirect, brain routing). ✓
- [ ] No "TBD/TODO" left in any task body. ✓
- [ ] Type consistency: `AgentRunRecord` fields used in tests match the dataclass; `FollowupAction` mode strings used in tests match the Literal in the model. ✓
- [ ] No reference to a function/class not defined in this plan or in existing code. ✓
- [ ] All new files have explicit paths. ✓
- [ ] All commits use the project's format (no skipped hooks, Co-Authored-By present). ✓
- [ ] CLAUDE.md edits are explicitly NOT committed (per project memory). ✓
