# Tailored Chat Output — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a question-aware shared formatter LLM between each module's output and the customer-facing text, replacing the templated `format_allocation_chat_brief` and the generic `_NARRATE_SYSTEM`/`_EDUCATE_SYSTEM`/`_COUNTERFACTUAL_NARRATE_SYSTEM` prompts in asset_allocation, then mirroring the pattern in rebalancing.

**Architecture:** New shared package `app/services/ai_bridge/answer_formatter/` owns the LLM call, house-style preamble, `FactsPack` type alias, and `FormatterFailure` exception. Each module exposes `build_<module>_facts_pack(output) -> dict` (in `service.py`) and `_<MODULE>_FORMATTER_BODY` constant (in `chat.py`); the existing per-module action classifier (`_detect_action`) stays untouched. The pre-existing templated formatter is renamed to `build_fallback_brief` and becomes the LLM-failure safety net.

**Tech Stack:** Python 3.9+, FastAPI, SQLAlchemy async, Pydantic v2, LangChain Anthropic (Haiku 4.5), Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-05-01-tailored-chat-output-design.md`.

---

## Phase 1 — Foundation + Asset Allocation Migration (independently shippable)

After Phase 1, `asset_allocation` chat output is fully question-aware. Rebalancing remains on its templated brief — no regression there since the renamed function is a no-op rename.

### Task 1: Alembic migration — add formatter columns to `chat_ai_module_runs`

**Files:**
- Create: `alembic/versions/<rev>_add_formatter_columns_to_chat_ai_module_run.py`
- Modify: `app/models/chat_ai_module_run.py` (add the columns to the ORM model)

- [ ] **Step 1: Generate the empty revision**

Run:
```bash
alembic revision -m "add_formatter_columns_to_chat_ai_module_run"
```

This creates a new file under `alembic/versions/` with a generated revision id. Note the path it printed — refer to it as `<new_rev_file>` below.

- [ ] **Step 2: Fill in the migration**

Edit `<new_rev_file>` so the body matches:

```python
"""Add formatter columns to chat_ai_module_runs.

Revision ID: <auto>
Revises: ee8987d840c5
Create Date: 2026-05-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "<auto>"
down_revision: Union[str, None] = "ee8987d840c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_ai_module_runs", sa.Column("formatter_invoked", sa.Boolean(), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("formatter_succeeded", sa.Boolean(), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("formatter_latency_ms", sa.Integer(), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("formatter_error_class", sa.String(length=128), nullable=True))
    op.add_column("chat_ai_module_runs", sa.Column("action_mode", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_ai_module_runs", "action_mode")
    op.drop_column("chat_ai_module_runs", "formatter_error_class")
    op.drop_column("chat_ai_module_runs", "formatter_latency_ms")
    op.drop_column("chat_ai_module_runs", "formatter_succeeded")
    op.drop_column("chat_ai_module_runs", "formatter_invoked")
```

Replace `<auto>` with the revision id Alembic generated. Verify `down_revision` matches the previous head (`alembic heads` should print `ee8987d840c5` before this migration is added).

- [ ] **Step 3: Mirror columns in the ORM model**

Edit `app/models/chat_ai_module_run.py`. Add these `Mapped` declarations after `output_payload` (line 44) and before `created_at`:

```python
    formatter_invoked: Mapped[Optional[bool]] = mapped_column(sa.Boolean, nullable=True)
    formatter_succeeded: Mapped[Optional[bool]] = mapped_column(sa.Boolean, nullable=True)
    formatter_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    formatter_error_class: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    action_mode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
```

Add `import sqlalchemy as sa` at the top if not already imported (it is — sa is used via `sa.Boolean` here, which requires the import; if linting complains, switch to `from sqlalchemy import Boolean` and use `Boolean` directly).

- [ ] **Step 4: Run the migration locally and verify it round-trips**

Run:
```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

Expected: each command exits 0. After the final upgrade, the columns exist on `chat_ai_module_runs`. Verify with:
```bash
psql $DATABASE_URL -c "\d chat_ai_module_runs" | grep -E "formatter_|action_mode"
```

Expected output: 5 lines listing the new columns.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/*formatter*.py app/models/chat_ai_module_run.py
git commit -m "feat(telemetry): add formatter columns to chat_ai_module_runs"
```

---

### Task 2: Shared `answer_formatter` package — types, exception, house style

**Files:**
- Create: `app/services/ai_bridge/answer_formatter/__init__.py`
- Create: `app/services/ai_bridge/answer_formatter/formatter.py`
- Create: `app/services/ai_bridge/answer_formatter/tests/__init__.py`
- Create: `app/services/ai_bridge/answer_formatter/tests/test_formatter.py`

This task lays down the contract types and the prompt-assembly logic, but **does not** make a real LLM call yet (Task 3 wires the LLM). That keeps tests fully synchronous and lets us verify prompt assembly in isolation.

- [ ] **Step 1: Write the failing test for prompt assembly**

Create `app/services/ai_bridge/answer_formatter/tests/__init__.py` (empty file).

Create `app/services/ai_bridge/answer_formatter/tests/test_formatter.py`:

```python
"""Tests for the shared answer_formatter — prompt assembly + types + fallback."""

from __future__ import annotations

import pytest

from app.services.ai_bridge.answer_formatter import (
    FORMATTER_HOUSE_STYLE,
    FormatterFailure,
    assemble_prompt,
)


def test_assemble_prompt_includes_house_style_and_body():
    prompt = assemble_prompt(
        question="why so much in debt?",
        action_mode="narrate",
        module_name="asset_allocation",
        facts_pack={"risk_score": 5.5, "asset_class_mix_pct": {"equity": 40.0, "debt": 51.0, "others": 9.0}},
        body_prompt="MODULE-BODY",
        history=[{"role": "user", "content": "what's my mix?"}],
        profile={"age": 39, "total_corpus_inr": 8_000_000},
    )
    assert FORMATTER_HOUSE_STYLE in prompt["system"]
    assert "MODULE-BODY" in prompt["system"]
    assert "why so much in debt?" in prompt["user"]
    assert "narrate" in prompt["user"]
    assert "5.5" in prompt["user"]
    assert "40.0" in prompt["user"]


def test_assemble_prompt_truncates_long_history():
    long_history = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    prompt = assemble_prompt(
        question="?", action_mode="narrate", module_name="x",
        facts_pack={}, body_prompt="b", history=long_history, profile={},
    )
    # Only the last 6 history entries should appear.
    assert "msg 49" in prompt["user"]
    assert "msg 0" not in prompt["user"]


def test_house_style_contains_required_prohibitions():
    """Guard rail: prohibitions must be present so future edits don't drop them."""
    text = FORMATTER_HOUSE_STYLE.lower()
    assert "never recommend" in text or "no specific fund" in text
    assert "never invent" in text or "do not invent" in text


def test_formatter_failure_is_an_exception():
    err = FormatterFailure("boom")
    assert isinstance(err, Exception)
    assert "boom" in str(err)
```

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/answer_formatter/tests/test_formatter.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.services.ai_bridge.answer_formatter'`.

- [ ] **Step 3: Create the package skeleton**

Create `app/services/ai_bridge/answer_formatter/__init__.py`:

```python
"""Shared question-aware answer formatter.

Public API:
    format_answer(...)     — async LLM call producing customer-facing text
    assemble_prompt(...)   — pure function building the prompt dict (system + user)
    FORMATTER_HOUSE_STYLE  — shared brand-voice preamble
    FactsPack              — type alias for the per-module facts dict
    ActionMode             — Literal of action mode strings the formatter accepts
    FormatterFailure       — raised when the LLM call fails or returns unusable text
"""

from app.services.ai_bridge.answer_formatter.formatter import (
    ActionMode,
    FORMATTER_HOUSE_STYLE,
    FactsPack,
    FormatterFailure,
    assemble_prompt,
    format_answer,
)

__all__ = [
    "ActionMode",
    "FORMATTER_HOUSE_STYLE",
    "FactsPack",
    "FormatterFailure",
    "assemble_prompt",
    "format_answer",
]
```

Create `app/services/ai_bridge/answer_formatter/formatter.py`:

```python
"""Shared answer-formatter implementation.

Single-file module: house-style preamble, FactsPack alias, ActionMode literal,
FormatterFailure exception, prompt-assembly helper, and the async LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Module-supplied dict — flat-ish, JSON-serializable, ≤ ~1500 tokens.
FactsPack = dict[str, Any]

# Modes that pass through the formatter. clarify / redirect bypass it.
ActionMode = Literal[
    "compute",
    "narrate",
    "educate",
    "recompute_full",
    "recompute_with_overrides",
    "counterfactual_explore",
]


class FormatterFailure(Exception):
    """Raised when the formatter LLM call fails or returns unusable text.

    Bridges catch this and fall back to the deterministic templated brief.
    """


# ---------------------------------------------------------------------------
# House style
# ---------------------------------------------------------------------------

FORMATTER_HOUSE_STYLE = """You are Prozpr, an Indian financial advisor speaking
to a customer about their goal-based investment plan. Tone: warm, specific,
concise. Length: 4-8 sentences unless the question demands more.

Hard rules:
- Never recommend a specific mutual fund, ISIN, or scheme name.
- Never invent numbers. Cite only values present in the FACTS_PACK below.
- Let the customer's QUESTION shape the response. Do not default to a fixed
  rendering order — answer what was asked.
- Use ₹ for amounts; render as "₹X,XX,XXX" (Indian numbering) or "₹X lakh" /
  "₹X crore" where natural.
- When the question can't be answered from the FACTS_PACK, say so plainly and
  offer a next step.

This is general information, not personalized advice. Do not promise outcomes.
"""


class _Prompt(TypedDict):
    system: str
    user: str


# ---------------------------------------------------------------------------
# Prompt assembly (pure)
# ---------------------------------------------------------------------------

def assemble_prompt(
    *,
    question: str,
    action_mode: str,
    module_name: str,
    facts_pack: FactsPack,
    body_prompt: str,
    history: list[dict[str, Any]],
    profile: dict[str, Any],
) -> _Prompt:
    """Build the (system, user) prompt pair. Pure — no LLM call."""
    system = "\n\n".join([FORMATTER_HOUSE_STYLE, body_prompt])
    history_lines = [
        f"{m.get('role','user')}: {m.get('content','')}"
        for m in (history or [])[-6:]
    ]
    user = (
        f"MODULE: {module_name}\n"
        f"ACTION_MODE: {action_mode}\n\n"
        f"FACTS_PACK:\n{json.dumps(facts_pack, default=str)}\n\n"
        f"PROFILE:\n{json.dumps(profile, default=str)}\n\n"
        f"RECENT_HISTORY:\n" + "\n".join(history_lines) + "\n\n"
        f"CUSTOMER_QUESTION: {question}"
    )
    return {"system": system, "user": user}


# ---------------------------------------------------------------------------
# Async LLM call (filled in in Task 3)
# ---------------------------------------------------------------------------

async def format_answer(
    *,
    question: str,
    action_mode: str,
    module_name: str,
    facts_pack: FactsPack,
    body_prompt: str,
    history: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    """Stub — Task 3 wires the LangChain Anthropic call."""
    raise NotImplementedError("format_answer is wired in Task 3")
```

- [ ] **Step 4: Run the test, verify it passes**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/answer_formatter/tests/test_formatter.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/answer_formatter/
git commit -m "feat(answer_formatter): add shared package skeleton with types + house style"
```

---

### Task 3: Wire the formatter LLM call

**Files:**
- Modify: `app/services/ai_bridge/answer_formatter/formatter.py` (replace the `format_answer` stub with the real LangChain call)
- Modify: `app/services/ai_bridge/answer_formatter/tests/test_formatter.py` (add LLM-mocked tests)

- [ ] **Step 1: Add failing tests for `format_answer` success and failure paths**

Append to `app/services/ai_bridge/answer_formatter/tests/test_formatter.py`:

```python
import asyncio
from unittest.mock import patch

from app.services.ai_bridge.answer_formatter import format_answer


def _call(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_format_answer_returns_text_on_success():
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        return_value="Here's your tailored answer.",
    ):
        out = asyncio.run(format_answer(
            question="?", action_mode="narrate", module_name="x",
            facts_pack={"k": 1}, body_prompt="b", history=[], profile={},
        ))
    assert out == "Here's your tailored answer."


def test_format_answer_raises_formatter_failure_on_empty_response():
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        return_value="",
    ):
        with pytest.raises(FormatterFailure):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))


def test_format_answer_raises_formatter_failure_on_llm_exception():
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        side_effect=RuntimeError("api down"),
    ):
        with pytest.raises(FormatterFailure):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))
```

- [ ] **Step 2: Run the new tests, verify they fail**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/answer_formatter/tests/test_formatter.py -v
```

Expected: 3 failures with `NotImplementedError: format_answer is wired in Task 3`.

- [ ] **Step 3: Replace the `format_answer` stub with the real LangChain Anthropic call**

In `app/services/ai_bridge/answer_formatter/formatter.py`, replace the stub with:

```python
async def format_answer(
    *,
    question: str,
    action_mode: str,
    module_name: str,
    facts_pack: FactsPack,
    body_prompt: str,
    history: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    """Async Haiku call. Raises FormatterFailure on any failure mode.

    Caller is expected to wrap in try/except and fall back to a templated brief.
    """
    prompt = assemble_prompt(
        question=question, action_mode=action_mode, module_name=module_name,
        facts_pack=facts_pack, body_prompt=body_prompt,
        history=history, profile=profile,
    )
    try:
        text = await _invoke_llm(prompt["system"], prompt["user"])
    except Exception as exc:
        raise FormatterFailure(f"formatter_llm_call_failed: {type(exc).__name__}") from exc

    if not text or not text.strip():
        raise FormatterFailure("formatter_llm_returned_empty")
    return text


async def _invoke_llm(system_text: str, user_text: str) -> str:
    """Single Haiku 4.5 call; isolated so tests can patch it."""
    # Imported lazily to keep test stubs cheap.
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.config import get_settings

    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=600,
    )
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    return getattr(raw, "content", "") or ""
```

- [ ] **Step 4: Run the tests, verify they pass**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/answer_formatter/tests/test_formatter.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/answer_formatter/
git commit -m "feat(answer_formatter): wire Haiku call with FormatterFailure on errors"
```

---

### Task 4: AA — rename `format_allocation_chat_brief` → `build_fallback_brief`

Pure mechanical refactor — no behavior change. Done as a separate task so the rename can be reviewed/reverted in isolation.

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation/service.py` (rename definition)
- Modify: `app/services/ai_bridge/asset_allocation/__init__.py` (rename re-export)
- Modify: `app/services/ai_bridge/asset_allocation/chat.py` (update import + 2 call sites)
- Modify: `app/services/ai_bridge/ailax_flow.py` (update import + 1 call site)
- Modify: `app/services/ai_bridge/asset_allocation/tests/test_chat.py` (update 3 patch targets)
- Modify: `app/services/ai_bridge/__init__.py` (if it re-exports — check)

- [ ] **Step 1: Rename the function definition**

In `app/services/ai_bridge/asset_allocation/service.py`:

- Line 149: `def format_allocation_chat_brief(` → `def build_fallback_brief(`
- Line 242: `return f"Based on your question: {user_question}\n\n{format_allocation_chat_brief(output, 'full')}"` → `return f"Based on your question: {user_question}\n\n{build_fallback_brief(output, 'full')}"`

- [ ] **Step 2: Update all call sites in one sweep**

Run:
```bash
grep -rln "format_allocation_chat_brief" --include="*.py" | xargs grep -l "format_allocation_chat_brief"
```

Replace `format_allocation_chat_brief` with `build_fallback_brief` in every file the grep returned, including:

- `app/services/ai_bridge/asset_allocation/__init__.py` (lines 12, 22)
- `app/services/ai_bridge/asset_allocation/chat.py` (lines 32, 251, 322)
- `app/services/ai_bridge/ailax_flow.py` (lines 20, 99)
- `app/services/ai_bridge/asset_allocation/tests/test_chat.py` (lines 86, 212, 238)

If `app/services/ai_bridge/__init__.py` re-exports the symbol, update there too.

- [ ] **Step 3: Verify no stragglers**

Run:
```bash
grep -rn "format_allocation_chat_brief" --include="*.py" | grep -v __pycache__ | grep -v archive
```

Expected: no output.

- [ ] **Step 4: Run the existing test suites — all green**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests app/services/ai_bridge/answer_formatter/tests -v
```

Expected: all previously-green tests still pass (22 from `asset_allocation/tests` + 7 from `answer_formatter/tests` = 29).

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/
git commit -m "refactor(asset_allocation): rename format_allocation_chat_brief to build_fallback_brief"
```

---

### Task 5: AA — add `build_aa_facts_pack` in `service.py`

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation/service.py` (add new function after `build_fallback_brief`)
- Create: `app/services/ai_bridge/asset_allocation/tests/test_service.py`

- [ ] **Step 1: Write the failing facts-pack test**

Create `app/services/ai_bridge/asset_allocation/tests/test_service.py`:

```python
"""Unit tests for asset_allocation/service.py: facts pack + fallback brief."""

from __future__ import annotations

import json

import pytest

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from asset_allocation_pydantic import AllocationInput, Goal, run_allocation  # type: ignore[import-not-found]
from asset_allocation_pydantic.steps._rationale_llm import _fallback_response  # type: ignore[import-not-found]

from app.services.ai_bridge.asset_allocation.service import (
    build_aa_facts_pack,
    build_fallback_brief,
)


def _no_llm(_summary, bucket_allocations, _aggregated):
    return _fallback_response(bucket_allocations)


@pytest.fixture
def sample_output():
    inp = AllocationInput(
        effective_risk_score=5.5,
        age=39,
        annual_income=2_000_000,
        osi=0.4,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=8_000_000,
        monthly_household_expense=80_000,
        tax_regime="new",
        effective_tax_rate=30.0,
        goals=[
            Goal(
                goal_name="Retirement",
                time_to_goal_months=240,
                amount_needed=40_000_000,
                goal_priority="non_negotiable",
            ),
        ],
    )
    return run_allocation(inp, rationale_fn=_no_llm)


def test_facts_pack_is_a_plain_dict(sample_output):
    pack = build_aa_facts_pack(sample_output)
    assert isinstance(pack, dict)
    assert pack  # non-empty


def test_facts_pack_contains_expected_top_level_keys(sample_output):
    pack = build_aa_facts_pack(sample_output)
    assert "risk_score" in pack
    assert "total_corpus_inr" in pack
    assert "asset_class_mix_pct" in pack
    assert "by_horizon" in pack
    assert "goals" in pack


def test_facts_pack_omits_fund_and_isin(sample_output):
    pack = build_aa_facts_pack(sample_output)
    blob = json.dumps(pack).lower()
    for forbidden in ("isin", "recommended_fund", "fund_mapping", "sub_category"):
        assert forbidden not in blob, f"facts pack leaks {forbidden}"


def test_facts_pack_is_under_token_budget(sample_output):
    pack = build_aa_facts_pack(sample_output)
    # Rough upper bound: 1500 tokens ≈ 6000 characters as JSON.
    assert len(json.dumps(pack)) < 6000


def test_facts_pack_is_deterministic(sample_output):
    a = build_aa_facts_pack(sample_output)
    b = build_aa_facts_pack(sample_output)
    assert a == b


def test_fallback_brief_is_non_empty(sample_output):
    text = build_fallback_brief(sample_output, "full")
    assert text.strip()
    assert "goal-based allocation" in text.lower()
```

- [ ] **Step 2: Run, verify failure**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests/test_service.py -v
```

Expected: collection or import error — `cannot import name 'build_aa_facts_pack' from app.services.ai_bridge.asset_allocation.service`.

- [ ] **Step 3: Implement `build_aa_facts_pack`**

In `app/services/ai_bridge/asset_allocation/service.py`, add the function below the renamed `build_fallback_brief` (so it sits with output-shaping logic):

```python
def build_aa_facts_pack(output: GoalAllocationOutput) -> dict[str, Any]:
    """Curated facts the LLM is allowed to cite.

    Keep small. Customer-tellable fields only — no internal subgroup keys,
    no fund/ISIN, no SEBI sub-categories.
    """
    cs = output.client_summary
    acb = output.asset_class_breakdown
    actual = acb.actual

    by_horizon = []
    for split in actual.per_bucket:
        if (split.equity + split.debt + split.others) <= 0:
            continue
        by_horizon.append({
            "horizon": split.bucket,
            "amount_inr": split.equity + split.debt + split.others,
            "mix_pct": {
                "equity": split.equity_pct,
                "debt": split.debt_pct,
                "others": split.others_pct,
            },
        })

    goals = []
    for b in output.bucket_allocations:
        for g in b.goals:
            goals.append({
                "name": g.goal_name,
                "amount_needed_inr": g.amount_needed,
                "horizon_months": g.time_to_goal_months,
                "bucket": b.bucket,
                "rationale": b.goal_rationales.get(g.goal_name),
            })

    future = [
        {
            "horizon": fi.bucket,
            "monthly_inr": fi.future_investment_amount,
            "purpose": fi.message,
        }
        for fi in output.future_investments_summary
    ]

    return {
        "risk_score": cs.effective_risk_score,
        "age": cs.age,
        "total_corpus_inr": output.grand_total,
        "asset_class_mix_pct": {
            "equity": actual.equity_total_pct,
            "debt": actual.debt_total_pct,
            "others": actual.others_total_pct,
        },
        "asset_class_mix_inr": {
            "equity": actual.equity_total,
            "debt": actual.debt_total,
            "others": actual.others_total,
        },
        "by_horizon": by_horizon,
        "goals": goals,
        "future_investments": future,
    }
```

- [ ] **Step 4: Run, verify pass**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests/test_service.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/
git commit -m "feat(asset_allocation): add build_aa_facts_pack and unit tests"
```

---

### Task 6: AA — add `_AA_FORMATTER_BODY` constant in `chat.py`

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation/chat.py` (add new constant; keep existing prompts for now — Task 7+ rewires them)

- [ ] **Step 1: Add the constant**

In `app/services/ai_bridge/asset_allocation/chat.py`, after the existing `_DETECT_SYSTEM` constant (around line 148) and before `_NARRATE_SYSTEM`, add:

```python
_AA_FORMATTER_BODY = """You are answering a customer's question about their
goal-based asset allocation plan. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  risk_score: number — customer's effective risk score (1-10)
  age: int
  total_corpus_inr: number — total invested corpus
  asset_class_mix_pct: {equity, debt, others} as percentages of total
  asset_class_mix_inr: {equity, debt, others} as ₹ amounts
  by_horizon: list of {horizon: emergency|short_term|medium_term|long_term,
              amount_inr, mix_pct: {equity, debt, others}}
  goals: list of {name, amount_needed_inr, horizon_months, bucket, rationale}
  future_investments: list of {horizon, monthly_inr, purpose}

ACTION_MODE tells you the situation:
  compute                     — first-time view of a fresh plan; introduce it
                                in customer-friendly terms shaped by their question.
  narrate                     — they're asking about the existing plan.
                                Cite specific numbers from the facts pack to
                                ground the answer; do not list every section.
  educate                     — they're asking what something means.
                                Explain in plain language, then tie it to
                                their facts pack.
  recompute_full              — they asked to re-run the plan with current
                                inputs. Acknowledge the re-run and highlight
                                what changed.
  recompute_with_overrides    — they locked in a new plan with changes.
                                Lead with what changed and the new mix.
  counterfactual_explore      — hypothetical-only result. Open with
                                "this is hypothetical, not your saved plan",
                                then compare to the saved plan.

Answer the customer's question. Do not default to a fixed template — what they
asked dictates the structure of the response.
"""
```

- [ ] **Step 2: Sanity check — module still imports**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -c "from app.services.ai_bridge.asset_allocation import chat; print(chat._AA_FORMATTER_BODY[:50])"
```

Expected: prints the first 50 chars of the body prompt.

- [ ] **Step 3: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/chat.py
git commit -m "feat(asset_allocation): add _AA_FORMATTER_BODY constant"
```

---

### Task 7: AA — rewire all formatter-applicable paths through `format_answer`

This is the largest behavior-change task. Five paths get rewired (`_first_turn_run_engine`, `_dispatch_action` narrate / educate, `_counterfactual_explore`, `_recompute_with_overrides`); three are deleted (`_narrate_with_llm`, `_educate_with_llm`, `_narrate_counterfactual`, `_free_text_call`).

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation/chat.py`
- Modify: `app/services/ai_bridge/asset_allocation/tests/test_chat.py`

- [ ] **Step 1: Update test mocks first (TDD: red)**

In `app/services/ai_bridge/asset_allocation/tests/test_chat.py`, replace patches of `_narrate_with_llm`, `_educate_with_llm`, `_narrate_counterfactual`, and `format_allocation_chat_brief` (now `build_fallback_brief`) with patches of `format_answer` from the shared package. Concretely:

- Replace each occurrence of `patch.object(mod, "_narrate_with_llm", new=AsyncMock(return_value="..."))` with:
  ```python
  patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
        new=AsyncMock(return_value="..."))
  ```
- Same substitution for `_educate_with_llm`, `_narrate_counterfactual`.
- Replace each `patch.object(mod, "build_fallback_brief", return_value="...")` with the same `format_answer` mock pattern (the brief is now only the fallback path; the happy path is the formatter).

Add one new test asserting fallback wiring:

```python
def test_first_turn_falls_back_to_brief_on_formatter_failure(self):
    outcome = _engine_outcome_with_ids()
    from app.services.ai_bridge.answer_formatter import FormatterFailure

    with patch.object(mod, "compute_allocation_result",
                      new=AsyncMock(return_value=outcome)), \
         patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
               new=AsyncMock(side_effect=FormatterFailure("boom"))), \
         patch("app.services.ai_bridge.asset_allocation.chat.build_fallback_brief",
               return_value="fallback brief text"):
        result = asyncio.run(mod.handle(_ctx("plan my retirement")))

    self.assertEqual(result.text, "fallback brief text")
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests/test_chat.py -v
```

Expected: multiple failures; chat.py still calls the old `_narrate_with_llm` etc.

- [ ] **Step 3: Add the formatter import + helper at the top of `chat.py`**

In `app/services/ai_bridge/asset_allocation/chat.py`, add to the imports block (after the existing imports around line 36):

```python
from app.services.ai_bridge.answer_formatter import (
    FormatterFailure,
    format_answer,
)
```

Replace the existing `service.py` import line so it pulls `build_aa_facts_pack` and `build_fallback_brief` (the latter was renamed in Task 4):

```python
from app.services.ai_bridge.asset_allocation.service import (
    build_aa_facts_pack,
    build_fallback_brief,
    compute_allocation_result,
)
```

Add a new helper near the bottom of the module (before `_ainvoke`). Note: this helper takes `ctx` from the start so Task 9 can layer telemetry into it without a signature change:

```python
async def _format_or_fallback(
    *,
    ctx: TurnContext,
    output: Any,
    action_mode: str,
    spine_mode: str,
) -> str:
    """Run the formatter; fall back to the templated brief on failure.

    Task 9 layers telemetry (timing + ChatAiModuleRun row) into this body.
    Signature stays stable so Task 9 doesn't ripple through call sites.
    """
    try:
        facts_pack = build_aa_facts_pack(output)
        return await format_answer(
            question=ctx.user_question,
            action_mode=action_mode,
            module_name="asset_allocation",
            facts_pack=facts_pack,
            body_prompt=_AA_FORMATTER_BODY,
            history=ctx.conversation_history or [],
            profile=_profile_dict(ctx),
        )
    except FormatterFailure as exc:
        logger.error(
            "formatter_failed",
            extra={
                "module": "asset_allocation",
                "mode": action_mode,
                "error_class": type(exc).__name__,
            },
        )
        return build_fallback_brief(output, spine_mode)
```

- [ ] **Step 4: Add the `_profile_dict` helper**

Near the top of the helper section (just before `_ainvoke`), add:

```python
def _profile_dict(ctx: TurnContext) -> dict[str, Any]:
    """Pull the customer's profile fields the formatter cares about."""
    user = ctx.user_ctx
    return {
        "age": getattr(user, "age", None) or _years_since(getattr(user, "date_of_birth", None)),
        "first_name": getattr(user, "first_name", None),
    }


def _years_since(dob: Any) -> int | None:
    if dob is None:
        return None
    from datetime import date
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _rehydrate_last_alloc_output(last_alloc: AgentRunRecord) -> Any:
    """Parse the persisted allocation_result JSON back into a GoalAllocationOutput.

    Used on follow-up turns when we don't re-run the engine but need the typed
    output to feed `build_aa_facts_pack` and the fallback brief.
    """
    from asset_allocation_pydantic.models import GoalAllocationOutput  # type: ignore[import-not-found]
    payload = (last_alloc.output_payload or {}).get("allocation_result") or {}
    return GoalAllocationOutput.model_validate(payload)
```

- [ ] **Step 5: Rewire `_first_turn_run_engine`**

In `_first_turn_run_engine` (lines ~235-256), after the blocking-message / no-result guards, replace:

```python
    text = build_fallback_brief(outcome.result, "full")
```

with:

```python
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result, action_mode="compute", spine_mode="full",
    )
```

- [ ] **Step 6: Rewire `_recompute_with_overrides`**

In `_recompute_with_overrides` (lines ~295-327), replace:

```python
    text = build_fallback_brief(outcome.result, "full")
```

with:

```python
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result,
        action_mode="recompute_with_overrides", spine_mode="full",
    )
```

- [ ] **Step 7: Rewire narrate / educate paths in `_dispatch_action`**

Edit `_dispatch_action` (lines ~202-228). Replace the two branches that call `_narrate_with_llm` / `_educate_with_llm`:

Replace:
```python
    if action.mode == "narrate":
        text = await _narrate_with_llm(last_alloc, ctx)
        return ChatHandlerResult(text=text)

    if action.mode == "educate":
        text = await _educate_with_llm(last_alloc, ctx)
        return ChatHandlerResult(text=text)
```

With:
```python
    if action.mode in ("narrate", "educate"):
        output = _rehydrate_last_alloc_output(last_alloc)
        text = await _format_or_fallback(
            ctx=ctx, output=output, action_mode=action.mode, spine_mode="full",
        )
        return ChatHandlerResult(text=text)
```

- [ ] **Step 8: Rewire `_counterfactual_explore`**

In `_counterfactual_explore` (lines ~259-287), replace the closing block:

```python
    text = await _narrate_counterfactual(last_alloc, ctx, outcome.result, overrides)
    return ChatHandlerResult(text=text)
```

with:

```python
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result,
        action_mode="counterfactual_explore", spine_mode="counterfactual",
    )
    return ChatHandlerResult(text=text)
```

Note: `clarify` and `redirect` paths are **not** modified — they keep their deterministic templates (per spec, formatter bypassed).

- [ ] **Step 9: Run tests, verify they pass**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests -v
```

Expected: all `test_chat.py` cases plus `test_service.py` pass.

- [ ] **Step 10: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/
git commit -m "feat(asset_allocation): route compute/narrate/educate/recompute/counterfactual through shared formatter"
```

---

### Task 8: AA — delete dead prompts and helpers

After Task 7, the following symbols are unreferenced in production code (only in tests, which Task 7 already updated). Delete them.

**Files:**
- Modify: `app/services/ai_bridge/asset_allocation/chat.py`

- [ ] **Step 1: Verify each symbol is now unreferenced**

Run:
```bash
for sym in _NARRATE_SYSTEM _EDUCATE_SYSTEM _COUNTERFACTUAL_NARRATE_SYSTEM _narrate_with_llm _educate_with_llm _narrate_counterfactual _free_text_call _ainvoke_text; do
  echo "== $sym =="
  grep -rn "$sym" --include="*.py" | grep -v __pycache__ | grep -v archive
done
```

Expected: each symbol appears only in `chat.py` itself (definitions). If a test still references one, fix the test before deleting.

- [ ] **Step 2: Delete the dead constants**

In `app/services/ai_bridge/asset_allocation/chat.py`, delete:

- `_NARRATE_SYSTEM` (the multi-line constant around lines 150-155)
- `_EDUCATE_SYSTEM` (around lines 157-162)
- `_COUNTERFACTUAL_NARRATE_SYSTEM` (around lines 164-167)

- [ ] **Step 3: Delete the dead async functions**

In the same file, delete:

- `_narrate_with_llm` (around lines 381-384)
- `_educate_with_llm` (around lines 387-390)
- `_free_text_call` (around lines 393-419)
- `_narrate_counterfactual` (around lines 422-443)
- `_ainvoke_text` (around lines 461-end) — only if no remaining caller. Verify with `grep -n _ainvoke_text app/services/ai_bridge/asset_allocation/chat.py`.

Keep `_ainvoke` — `_detect_action` still uses it.

- [ ] **Step 4: Run tests**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/asset_allocation/chat.py
git commit -m "refactor(asset_allocation): delete now-unreferenced narrate/educate/counterfactual prompts"
```

---

### Task 9: AA — wire telemetry for formatter invocations

The formatter columns added in Task 1 need to be populated. Today, `record_ai_module_run` is called from `compute_allocation_result` (and possibly from chat handler paths). We need to extend it to accept the new fields and call sites to pass them.

**Files:**
- Modify: `app/services/ai_module_telemetry.py` (add 5 new params)
- Modify: `app/services/ai_bridge/asset_allocation/chat.py` (record formatter outcome at end of each formatter-using path)
- Modify: `app/services/ai_bridge/asset_allocation/tests/test_chat.py` (assert telemetry columns)

- [ ] **Step 1: Failing test for telemetry write**

Add to `app/services/ai_bridge/asset_allocation/tests/test_chat.py`:

```python
class FormatterTelemetryTests(unittest.TestCase):

    def test_first_turn_records_formatter_columns_on_success(self):
        outcome = _engine_outcome_with_ids()
        captured: dict[str, Any] = {}

        async def fake_record(*args, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="tailored answer")), \
             patch("app.services.ai_bridge.asset_allocation.chat.record_ai_module_run",
                   side_effect=fake_record):
            asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(captured.get("action_mode"), "compute")
        self.assertTrue(captured.get("formatter_invoked"))
        self.assertTrue(captured.get("formatter_succeeded"))
        self.assertIsNone(captured.get("formatter_error_class"))
        self.assertIsNotNone(captured.get("formatter_latency_ms"))

    def test_first_turn_records_formatter_columns_on_failure(self):
        from app.services.ai_bridge.answer_formatter import FormatterFailure
        outcome = _engine_outcome_with_ids()
        captured: dict[str, Any] = {}

        async def fake_record(*args, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(side_effect=FormatterFailure("api_down"))), \
             patch("app.services.ai_bridge.asset_allocation.chat.build_fallback_brief",
                   return_value="fallback"), \
             patch("app.services.ai_bridge.asset_allocation.chat.record_ai_module_run",
                   side_effect=fake_record):
            asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(captured.get("action_mode"), "compute")
        self.assertTrue(captured.get("formatter_invoked"))
        self.assertFalse(captured.get("formatter_succeeded"))
        self.assertEqual(captured.get("formatter_error_class"), "FormatterFailure")
```

Add `from typing import Any` and `import uuid` at the top if missing.

- [ ] **Step 2: Run, verify failure**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests/test_chat.py::FormatterTelemetryTests -v
```

Expected: failures — `record_ai_module_run` is not called from the chat path in the right place yet.

- [ ] **Step 3: Extend `record_ai_module_run` signature**

In `app/services/ai_module_telemetry.py`, add 5 keyword-only params to `record_ai_module_run`:

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
    # New formatter telemetry — all nullable.
    formatter_invoked: bool | None = None,
    formatter_succeeded: bool | None = None,
    formatter_latency_ms: int | None = None,
    formatter_error_class: str | None = None,
    action_mode: str | None = None,
) -> uuid.UUID | None:
```

Pass them into the `ChatAiModuleRun(...)` constructor:

```python
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
                formatter_invoked=formatter_invoked,
                formatter_succeeded=formatter_succeeded,
                formatter_latency_ms=formatter_latency_ms,
                formatter_error_class=formatter_error_class,
                action_mode=action_mode,
            )
```

- [ ] **Step 4: Layer telemetry into `_format_or_fallback`**

The helper signature stays the same (Task 7 introduced `_format_or_fallback(ctx, output, action_mode, spine_mode)`). This step adds timing + telemetry inside the body, so call sites are untouched.

Add the import at the top of `chat.py`:
```python
from app.services.ai_module_telemetry import record_ai_module_run
```

Replace the body of `_format_or_fallback` with:

```python
async def _format_or_fallback(
    *,
    ctx: TurnContext,
    output: Any,
    action_mode: str,
    spine_mode: str,
) -> str:
    """Run the formatter; fall back to the templated brief on failure.

    Records a ChatAiModuleRun row with formatter timing and success/failure.
    """
    import time
    started = time.monotonic()
    formatter_succeeded = False
    formatter_error_class: str | None = None
    try:
        facts_pack = build_aa_facts_pack(output)
        text = await format_answer(
            question=ctx.user_question,
            action_mode=action_mode,
            module_name="asset_allocation",
            facts_pack=facts_pack,
            body_prompt=_AA_FORMATTER_BODY,
            history=ctx.conversation_history or [],
            profile=_profile_dict(ctx),
        )
        formatter_succeeded = True
    except FormatterFailure as exc:
        formatter_error_class = type(exc).__name__
        logger.error(
            "formatter_failed",
            extra={"module": "asset_allocation", "mode": action_mode,
                   "error_class": formatter_error_class},
        )
        text = build_fallback_brief(output, spine_mode)
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        await record_ai_module_run(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            module="asset_allocation",
            reason=f"formatter:{action_mode}",
            duration_ms=latency_ms,
            formatter_invoked=True,
            formatter_succeeded=formatter_succeeded,
            formatter_latency_ms=latency_ms,
            formatter_error_class=formatter_error_class,
            action_mode=action_mode,
            emit_standard_log=False,
        )
    return text
```

Call sites (`_first_turn_run_engine`, `_recompute_with_overrides`, `_counterfactual_explore`, and the narrate/educate branch in `_dispatch_action`) need no changes — they already pass `ctx`.

- [ ] **Step 5: Run tests, verify pass**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/asset_allocation/tests -v
```

Expected: all green, including the two new `FormatterTelemetryTests`.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_module_telemetry.py app/services/ai_bridge/asset_allocation/
git commit -m "feat(asset_allocation): record formatter telemetry per chat turn"
```

---

### Task 10: Phase 1 verification — full suite + smoke

- [ ] **Step 1: Run all relevant suites**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest \
  app/services/ai_bridge/answer_formatter/tests \
  app/services/ai_bridge/asset_allocation/tests \
  app/services/ai_bridge/rebalancing/tests \
  AI_Agents/src/asset_allocation_pydantic/Testing -v
```

Expected: all pass. (Rebalancing is unchanged in Phase 1; suite stays green.)

- [ ] **Step 2: Manual eyeball — boot the app and try one chat turn**

```bash
uvicorn main:app --reload
```

In a second terminal, hit the chat endpoint with a real session and a real allocation question. Verify:
- The response text is non-templated (i.e. doesn't read like the old `format_allocation_chat_brief` output).
- A new row appears in `chat_ai_module_runs` with `module='asset_allocation'`, `reason='formatter:compute'`, and the formatter columns populated.

This is **manual eval gate** for Phase 1 — confirm at least one turn produces tailored output before declaring AA migration complete.

- [ ] **Step 3: Tag Phase 1 complete**

```bash
git tag phase1-tailored-chat-output
```

---

## Phase 2 — Rebalancing Migration (after Phase 1 lands)

Mirror of Phase 1 against the rebalancing module. Independent ship.

### Task 11: Rebalancing — define action taxonomy and `_detect_rebal_action`

Today, `app/services/ai_bridge/rebalancing/chat.py` is 36 lines: every turn calls `compute_rebalancing_result` and surfaces `outcome.formatted_text` (which is built inside the service via `format_rebalancing_chat_brief`). We need a classifier that decides re-run vs narrate vs clarify vs redirect.

**Files:**
- Modify: `app/services/ai_bridge/rebalancing/chat.py` (add `RebalanceAction`, `_DETECT_REBAL_SYSTEM`, `_detect_rebal_action`, `_ainvoke` helper; existing handler stays unchanged in this task — Task 13 rewires it)
- Modify: `app/services/ai_bridge/rebalancing/tests/test_chat.py` (create file if not present; add tests for classifier)

- [ ] **Step 1: Write failing tests for `_detect_rebal_action`**

Create `app/services/ai_bridge/rebalancing/tests/test_chat.py` if it doesn't exist:

```python
"""Tests for rebalancing chat handler."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge.rebalancing import chat as mod
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


def _agent_run(payload: dict | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="rebalancing",
        intent_detected="rebalancing",
        input_payload={},
        output_payload=payload or {"trades": []},
        created_at=__import__("datetime").datetime.utcnow(),
    )


def _ctx(question: str, *, last_run: AgentRunRecord | None = None) -> TurnContext:
    last_runs = {"rebalancing": last_run} if last_run else {}
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=date(1986, 1, 1), first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs=last_runs,
        active_intent="rebalancing",
    )


class DetectRebalActionTests(unittest.TestCase):

    def test_narrate_mode_for_explanation_question(self):
        with patch.object(mod, "_ainvoke",
                          new=AsyncMock(return_value=mod.RebalanceAction(mode="narrate"))):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("why are you selling X?")))
        self.assertEqual(action.mode, "narrate")

    def test_recompute_mode_for_explicit_rerun(self):
        with patch.object(mod, "_ainvoke",
                          new=AsyncMock(return_value=mod.RebalanceAction(mode="recompute"))):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("redo the trades")))
        self.assertEqual(action.mode, "recompute")

    def test_clarify_mode_carries_question(self):
        ret = mod.RebalanceAction(mode="clarify", clarification_question="Which fund?")
        with patch.object(mod, "_ainvoke", new=AsyncMock(return_value=ret)):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("change something")))
        self.assertEqual(action.mode, "clarify")
        self.assertEqual(action.clarification_question, "Which fund?")

    def test_redirect_mode_carries_reason(self):
        ret = mod.RebalanceAction(mode="redirect", redirect_reason="lock fund Y")
        with patch.object(mod, "_ainvoke", new=AsyncMock(return_value=ret)):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("keep fund Y")))
        self.assertEqual(action.mode, "redirect")
        self.assertIn("lock", action.redirect_reason)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify failure**

Run:
```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/rebalancing/tests/test_chat.py -v
```

Expected: import error — `RebalanceAction`, `_detect_rebal_action`, `_ainvoke` not in `chat.py`.

- [ ] **Step 3: Implement the action schema and classifier**

Replace the entire contents of `app/services/ai_bridge/rebalancing/chat.py` with:

```python
"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action schema
# ---------------------------------------------------------------------------

class RebalanceAction(BaseModel):
    mode: Literal["narrate", "recompute", "clarify", "redirect"]
    clarification_question: Optional[str] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Prompts and templates
# ---------------------------------------------------------------------------

_DETECT_REBAL_SYSTEM = """You decide how to handle a chat turn about a customer's
mutual fund rebalancing recommendation. Pick exactly one of four modes:

- "narrate" — they're asking about the existing recommendation
  ("why are you selling X?", "what's the tax impact?").
- "recompute" — they explicitly ask to re-run with current portfolio state
  ("rebalance again", "redo this with my latest holdings").
- "clarify" — they signal a direction without an actionable value.
  Compose a concise clarification question in `clarification_question`.
- "redirect" — they want something we can't do from chat (lock specific funds,
  change tax preferences, edit holdings). Set `redirect_reason` to a short
  description.
"""

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** or **Holdings** page and update "
    "the relevant inputs — I'll regenerate the rebalancing plan automatically."
)

_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific fund, action (sell/swap), "
    "or constraint?"
)


# ---------------------------------------------------------------------------
# Public handler — Task 13 fills this in. For now, keep the existing behavior.
# ---------------------------------------------------------------------------

@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Run the rebalancing pipeline for the current turn and forward the result."""
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
    )
    if outcome.blocking_message is not None:
        return ChatHandlerResult(
            text=outcome.blocking_message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
            chart=None,
        )
    return ChatHandlerResult(
        text=outcome.formatted_text or "",
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.recommendation_id,
        chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
    )


# ---------------------------------------------------------------------------
# LLM call — classifier for follow-up turns
# ---------------------------------------------------------------------------

async def _detect_rebal_action(
    last_run: AgentRunRecord, ctx: TurnContext,
) -> RebalanceAction:
    """One Haiku call returning a RebalanceAction."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=300,
    ).with_structured_output(RebalanceAction)
    snapshot = json.dumps(last_run.output_payload, default=str)[:6000]
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Most recent rebalancing snapshot (truncated):\n{snapshot}"
    )
    return await _ainvoke(llm, _DETECT_REBAL_SYSTEM, user_block)


async def _ainvoke(llm: Any, system_text: str, user_text: str) -> Any:
    """Structured-output invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    return await asyncio.to_thread(llm.invoke, messages)
```

Note: the `handle()` function is unchanged from today; Task 13 rewires it. This task adds the classifier scaffolding alongside.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/rebalancing/tests/test_chat.py -v
```

Expected: 4 `DetectRebalActionTests` pass. Existing rebalancing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/
git commit -m "feat(rebalancing): add _detect_rebal_action classifier with 4-mode taxonomy"
```

---

### Task 12: Rebalancing — `build_rebal_facts_pack` + rename templated formatter

The rebalancing engine returns a `RebalancingComputeResponse` (the type imported into `service.py`). This task adds a curated facts pack derived from that response, and renames `format_rebalancing_chat_brief` → `build_fallback_rebal_brief`.

**Files:**
- Modify: `app/services/ai_bridge/rebalancing/service.py` (add `build_rebal_facts_pack`)
- Modify: `app/services/ai_bridge/rebalancing/formatter.py` (rename `format_rebalancing_chat_brief` → `build_fallback_rebal_brief`)
- Modify: all import sites — `service.py` line 28 + line 229 (the call inside `compute_rebalancing_result`)
- Create: `app/services/ai_bridge/rebalancing/tests/test_service.py`

- [ ] **Step 1: Inspect `RebalancingComputeResponse` to know the available fields**

```bash
grep -n "class RebalancingComputeResponse\|class FundRowAfterStep5\|^class " AI_Agents/src/Rebalancing/models.py | head
```

Note the fields exposed on the response — `bucket_summaries`, `trade_actions`, `warnings`, `tax_summary`, etc. The exact set depends on the current `Rebalancing/models.py` schema; use whatever's there.

- [ ] **Step 2: Write failing tests for `build_rebal_facts_pack`**

Create `app/services/ai_bridge/rebalancing/tests/test_service.py`:

```python
"""Unit tests for rebalancing service helpers — facts pack + fallback brief."""

from __future__ import annotations

import json

import pytest

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()


def _build_min_response():
    """Build a minimal RebalancingComputeResponse for tests.

    Read AI_Agents/src/Rebalancing/models.py to see required fields. Construct
    the smallest valid instance — empty trades, empty warnings, total = 0.
    """
    from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]
    # Fill in required fields based on the model. Example skeleton — adjust to
    # match the model's required attributes:
    return RebalancingComputeResponse(
        rows=[],
        warnings=[],
        # ... add other required fields per the model definition
    )


def test_facts_pack_is_a_plain_dict():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack
    pack = build_rebal_facts_pack(_build_min_response())
    assert isinstance(pack, dict)


def test_facts_pack_omits_fund_and_isin():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack
    pack = build_rebal_facts_pack(_build_min_response())
    blob = json.dumps(pack).lower()
    for forbidden in ("isin", "recommended_fund"):
        assert forbidden not in blob


def test_facts_pack_under_token_budget():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack
    pack = build_rebal_facts_pack(_build_min_response())
    assert len(json.dumps(pack)) < 6000


def test_fallback_rebal_brief_is_non_empty():
    from app.services.ai_bridge.rebalancing.formatter import build_fallback_rebal_brief
    text = build_fallback_rebal_brief(_build_min_response(), used_cached_allocation=False)
    assert isinstance(text, str)
```

Note: this test uses a minimal in-memory response. If constructing one is tricky given the schema, swap in a fixture from the existing rebalancing test conftest (look in `app/services/ai_bridge/rebalancing/tests/conftest.py` for `RebalancingComputeResponse`-shaped fixtures).

- [ ] **Step 3: Run, verify failure**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/rebalancing/tests/test_service.py -v
```

Expected: ImportError — `build_rebal_facts_pack` does not exist; `build_fallback_rebal_brief` does not exist.

- [ ] **Step 4: Implement `build_rebal_facts_pack`**

In `app/services/ai_bridge/rebalancing/service.py`, after the `RebalancingRunOutcome` dataclass (line 78) and before `_user_has_mf_holdings`, add:

```python
def build_rebal_facts_pack(response: "RebalancingComputeResponse") -> dict[str, Any]:
    """Curated facts the LLM may cite. Customer-tellable only — no ISIN.

    Shape (subject to refinement during testing):
      {
        "total_portfolio_inr": <float>,
        "buys_total_inr": <float>,
        "sells_total_inr": <float>,
        "tax_impact_inr": <float>,
        "trade_count": int,
        "buckets": [{"asset_class": str, "target_pct": float, "current_pct": float, "drift_pct": float}, ...],
        "warnings": [<short_string>, ...],   # human-readable, <= 5 entries
      }

    Fields are derived from `response`; absent fields become 0/empty list.
    """
    rows = list(getattr(response, "rows", []) or [])
    warnings_list = list(getattr(response, "warnings", []) or [])

    buys_total = sum(
        float(getattr(r, "pass1_buy_amount", 0) or 0)
        for r in rows
    )
    sells_total = sum(
        float(getattr(r, "pass1_sell_amount", 0) or 0)
        for r in rows
    )
    tax_impact = float(getattr(response, "total_tax_payable", 0) or 0)
    total_portfolio = buys_total + sells_total  # placeholder; refine if response has a field

    buckets: list[dict[str, Any]] = []
    for bucket_summary in getattr(response, "bucket_summaries", []) or []:
        buckets.append({
            "asset_class": getattr(bucket_summary, "asset_class", None),
            "target_pct": float(getattr(bucket_summary, "target_pct", 0) or 0),
            "current_pct": float(getattr(bucket_summary, "current_pct", 0) or 0),
            "drift_pct": float(getattr(bucket_summary, "drift_pct", 0) or 0),
        })

    warnings: list[str] = []
    for w in warnings_list[:5]:
        msg = getattr(w, "message", None) or str(w)
        warnings.append(msg)

    return {
        "total_portfolio_inr": total_portfolio,
        "buys_total_inr": buys_total,
        "sells_total_inr": sells_total,
        "tax_impact_inr": tax_impact,
        "trade_count": sum(1 for r in rows if (
            float(getattr(r, "pass1_buy_amount", 0) or 0) > 0
            or float(getattr(r, "pass1_sell_amount", 0) or 0) > 0
        )),
        "buckets": buckets,
        "warnings": warnings,
    }
```

The `getattr` defensiveness handles real-world drift between this plan and the current `RebalancingComputeResponse` schema. **If a field listed above doesn't exist on the response,** verify by reading `AI_Agents/src/Rebalancing/models.py` and adapt the attribute names accordingly — the structure of the facts pack stays the same, only the source attributes change.

- [ ] **Step 5: Rename the templated formatter**

In `app/services/ai_bridge/rebalancing/formatter.py` line 202:

```python
def format_rebalancing_chat_brief(
```

Rename to:

```python
def build_fallback_rebal_brief(
```

Update import sites:

```bash
grep -rln "format_rebalancing_chat_brief" --include="*.py" | grep -v __pycache__
```

Replace each occurrence with `build_fallback_rebal_brief`. Specifically expect to update:
- `app/services/ai_bridge/rebalancing/service.py` line 28 (import) and line 229 (call site)

- [ ] **Step 6: Verify no stragglers**

```bash
grep -rn "format_rebalancing_chat_brief" --include="*.py" | grep -v __pycache__ | grep -v archive
```

Expected: no output.

- [ ] **Step 7: Run full rebalancing suite**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/rebalancing/tests -v
```

Expected: all green (pre-existing tests + new `test_service.py` cases).

- [ ] **Step 8: Commit**

```bash
git add app/services/ai_bridge/rebalancing/
git commit -m "feat(rebalancing): add build_rebal_facts_pack and rename templated formatter to build_fallback_rebal_brief"
```

---

### Task 13: Rebalancing — `_REBAL_FORMATTER_BODY` + rewire `chat.py`

**Files:**
- Modify: `app/services/ai_bridge/rebalancing/chat.py`
- Modify: `app/services/ai_bridge/rebalancing/tests/test_chat.py`

- [ ] **Step 1: Write failing test for handler routing**

Append to `app/services/ai_bridge/rebalancing/tests/test_chat.py`:

```python
class HandleRoutingTests(unittest.TestCase):

    def test_first_turn_runs_engine_and_calls_formatter(self):
        outcome = MagicMock(
            response=MagicMock(),
            blocking_message=None,
            allocation_snapshot_id=uuid.uuid4(),
            recommendation_id=uuid.uuid4(),
            chart=None,
        )
        with patch.object(mod, "compute_rebalancing_result",
                          new=AsyncMock(return_value=outcome)), \
             patch("app.services.ai_bridge.rebalancing.chat.format_answer",
                   new=AsyncMock(return_value="tailored")):
            result = asyncio.run(mod.handle(_ctx("rebalance my portfolio")))
        self.assertEqual(result.text, "tailored")

    def test_followup_clarify_bypasses_formatter(self):
        action = mod.RebalanceAction(mode="clarify", clarification_question="Which fund?")
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch("app.services.ai_bridge.rebalancing.chat.format_answer",
                   new=AsyncMock()) as fmt:
            result = asyncio.run(mod.handle(_ctx("change something", last_run=_agent_run())))
        self.assertEqual(result.text, "Which fund?")
        fmt.assert_not_called()

    def test_followup_narrate_does_not_re_run_engine(self):
        action = mod.RebalanceAction(mode="narrate")
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_rebalancing_result",
                          new=AsyncMock()) as engine, \
             patch("app.services.ai_bridge.rebalancing.chat.format_answer",
                   new=AsyncMock(return_value="explained")):
            result = asyncio.run(mod.handle(_ctx("why?", last_run=_agent_run({"response": {"rows": []}}))))
        self.assertEqual(result.text, "explained")
        engine.assert_not_called()

    def test_followup_recompute_re_runs_engine(self):
        action = mod.RebalanceAction(mode="recompute")
        outcome = MagicMock(
            response=MagicMock(),
            blocking_message=None,
            allocation_snapshot_id=uuid.uuid4(),
            recommendation_id=uuid.uuid4(),
            chart=None,
        )
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_rebalancing_result",
                          new=AsyncMock(return_value=outcome)), \
             patch("app.services.ai_bridge.rebalancing.chat.format_answer",
                   new=AsyncMock(return_value="redone")):
            result = asyncio.run(mod.handle(_ctx("redo", last_run=_agent_run())))
        self.assertEqual(result.text, "redone")
```

- [ ] **Step 2: Run, verify failure**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/rebalancing/tests/test_chat.py::HandleRoutingTests -v
```

Expected: failures because `chat.py` doesn't yet route follow-ups through `_detect_rebal_action`.

- [ ] **Step 3: Add `_REBAL_FORMATTER_BODY` and the helper, then rewrite `handle`**

In `app/services/ai_bridge/rebalancing/chat.py`, add this body prompt below the existing `_DETECT_REBAL_SYSTEM`:

```python
_REBAL_FORMATTER_BODY = """You are answering a customer's question about a
mutual-fund rebalancing recommendation. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  total_portfolio_inr: number — total invested corpus
  buys_total_inr: number — sum of recommended buy amounts
  sells_total_inr: number — sum of recommended sell amounts
  tax_impact_inr: number — estimated tax payable on the sells
  trade_count: int — number of trades in the recommendation
  buckets: list of {asset_class, target_pct, current_pct, drift_pct}
  warnings: list of short human-readable strings

ACTION_MODE tells you the situation:
  compute    — first-time rebalancing recommendation; introduce it shaped by
               the customer's question. If trade_count is 0, lead with that.
  narrate    — they're asking about the existing recommendation.
               Cite specific buckets / amounts to ground the answer.
  recompute  — they asked to re-run; acknowledge and lead with what changed.

Answer the customer's question. Do not list every bucket unless asked.
"""
```

Add the formatter and telemetry imports below the existing imports:

```python
from app.services.ai_bridge.answer_formatter import (
    FormatterFailure,
    format_answer,
)
from app.services.ai_bridge.rebalancing.formatter import build_fallback_rebal_brief
from app.services.ai_bridge.rebalancing.service import (
    build_rebal_facts_pack,
    compute_rebalancing_result,
)
from app.services.ai_module_telemetry import record_ai_module_run
```

(Replace the existing `from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result` line with the multi-name import above.)

Add the helper function alongside `_detect_rebal_action`:

```python
async def _format_or_fallback_rebal(
    *,
    ctx: TurnContext,
    response: Any,
    fallback_brief: str,
    action_mode: str,
) -> str:
    """Run the formatter; fall back to the precomputed templated brief on failure."""
    import time
    started = time.monotonic()
    formatter_succeeded = False
    formatter_error_class: str | None = None
    try:
        facts_pack = build_rebal_facts_pack(response)
        text = await format_answer(
            question=ctx.user_question,
            action_mode=action_mode,
            module_name="rebalancing",
            facts_pack=facts_pack,
            body_prompt=_REBAL_FORMATTER_BODY,
            history=ctx.conversation_history or [],
            profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        )
        formatter_succeeded = True
    except FormatterFailure as exc:
        formatter_error_class = type(exc).__name__
        logger.error("formatter_failed", extra={
            "module": "rebalancing", "mode": action_mode,
            "error_class": formatter_error_class,
        })
        text = fallback_brief
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        await record_ai_module_run(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            module="rebalancing",
            reason=f"formatter:{action_mode}",
            duration_ms=latency_ms,
            formatter_invoked=True,
            formatter_succeeded=formatter_succeeded,
            formatter_latency_ms=latency_ms,
            formatter_error_class=formatter_error_class,
            action_mode=action_mode,
            emit_standard_log=False,
        )
    return text
```

Replace the entire `handle` function with:

```python
@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    last_run = ctx.last_agent_runs.get("rebalancing")

    # First turn → run engine, format compute output.
    if last_run is None:
        outcome = await compute_rebalancing_result(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            db=ctx.db,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
        )
        if outcome.blocking_message is not None:
            return ChatHandlerResult(text=outcome.blocking_message, snapshot_id=None,
                                     rebalancing_recommendation_id=None, chart=None)
        text = await _format_or_fallback_rebal(
            ctx=ctx, response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="compute",
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=outcome.allocation_snapshot_id,
            rebalancing_recommendation_id=outcome.recommendation_id,
            chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
        )

    # Follow-up → classify.
    try:
        action = await _detect_rebal_action(last_run, ctx)
    except Exception as exc:
        logger.warning("detect_rebal_action failed (%s); falling back to narrate", exc)
        action = RebalanceAction(mode="narrate")

    if action.mode == "clarify":
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text, snapshot_id=None,
                                 rebalancing_recommendation_id=None, chart=None)

    if action.mode == "redirect":
        reason = action.redirect_reason or "change your trades"
        return ChatHandlerResult(text=_REDIRECT_TEMPLATE.format(reason=reason),
                                 snapshot_id=None, rebalancing_recommendation_id=None,
                                 chart=None)

    # narrate or recompute — both go through formatter; recompute also re-runs.
    if action.mode == "recompute":
        outcome = await compute_rebalancing_result(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            db=ctx.db,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
        )
        if outcome.blocking_message is not None:
            return ChatHandlerResult(text=outcome.blocking_message, snapshot_id=None,
                                     rebalancing_recommendation_id=None, chart=None)
        text = await _format_or_fallback_rebal(
            ctx=ctx, response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="recompute",
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=outcome.allocation_snapshot_id,
            rebalancing_recommendation_id=outcome.recommendation_id,
            chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
        )

    # narrate — use last_run.output_payload as the source. The persisted shape
    # is {"rebalancing_response": <model_dump>, "correlation_ids": {...}}; see
    # rebalancing/service.py compute_rebalancing_result telemetry write.
    response_payload = (last_run.output_payload or {}).get("rebalancing_response") or {}
    response = _rehydrate_response(response_payload)
    # No persisted formatted_text — rebuild the templated fallback inline if
    # the formatter fails. Note: build_fallback_rebal_brief expects the
    # typed response; if rehydration returned a dict, the fallback path will
    # raise inside the helper and we'll surface a generic error string.
    fallback = build_fallback_rebal_brief(response, used_cached_allocation=False) if not isinstance(response, dict) else ""
    text = await _format_or_fallback_rebal(
        ctx=ctx, response=response, fallback_brief=fallback, action_mode="narrate",
    )
    return ChatHandlerResult(text=text, snapshot_id=None,
                             rebalancing_recommendation_id=None, chart=None)


def _rehydrate_response(payload: dict[str, Any]) -> Any:
    """Best-effort rehydration of RebalancingComputeResponse from persisted JSON.

    Returns the typed pydantic model if validation succeeds; otherwise returns
    the raw dict (the facts-pack builder uses `getattr` so a dict still works
    for missing-attr defaults).
    """
    try:
        from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]
        return RebalancingComputeResponse.model_validate(payload)
    except Exception:
        return payload
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest app/services/ai_bridge/rebalancing/tests -v
```

Expected: all green (existing rebalancing tests still pass + the four new `HandleRoutingTests` from Step 1).

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_bridge/rebalancing/
git commit -m "feat(rebalancing): route compute/narrate/recompute through shared formatter, classify follow-ups"
```

---

### Task 14: Phase 2 verification

- [ ] **Step 1: Full test sweep**

```bash
PYTHONPATH=AI_Agents/src:. /usr/bin/python3 -m pytest \
  app/services/ai_bridge \
  AI_Agents/src/asset_allocation_pydantic/Testing -v
```

Expected: all green.

- [ ] **Step 2: Manual eyeball — both modules**

Boot the app, exercise one chat turn each in `asset_allocation` and `rebalancing` intents. Confirm:
- Both produce non-templated, question-shaped responses.
- `chat_ai_module_runs` rows for both modules carry the formatter columns.

- [ ] **Step 3: Tag Phase 2 complete**

```bash
git tag phase2-tailored-chat-output
```

---

## Follow-ups (not in this plan)

Captured here so they don't get lost; tracked separately:

- **Manual eval workbook** — a curated set of `(question, module_output)` cases scored against tailored outputs to measure quality and detect regressions across prompt iterations. Run before declaring rollout fully successful.
- **Streaming formatter output** — frontend/SSE plumbing.
- **Migrate `portfolio_query` / `general_chat` / `market_commentary`** to the same pattern (they're already LLM-driven; less urgent but worth consistency).
- **Per-module token budgets** — measured during Phase 1 manual eval; codified once we have data.
- **Prompt tuning iterations** post-ship driven by the manual eval workbook.
