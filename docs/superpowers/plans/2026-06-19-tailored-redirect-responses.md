# Tailored Redirect Responses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `out_of_scope` and `stock_advice` chat replies acknowledge the customer's actual question before redirecting, instead of returning a fixed canned string.

**Architecture:** Add one handler (`format_redirect_or_canned`) beside the existing `_oos_reply` in `general_chat_engine.py`. It resolves the canned line via `_oos_reply`; for the tailored sub-reasons it runs the shared `format_with_telemetry` (facts pack = the canned line as `boundary_message`), and on any formatter failure it returns that same canned line. The brain's classifier-only short-circuit calls this handler instead of returning the raw string.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy, LangChain `ChatAnthropic` (via the shared answer formatter), pytest (`asyncio_mode=auto`).

## Global Constraints

- **Not a git repository.** This tree has no git. The "Checkpoint" step ending each task is a **review gate**, not a `git commit`. Do not run git commands.
- **Run tests with:** `.venv-mac/bin/python -m pytest` from `Prozpr_Backend/` (config in `pyproject.toml`, `asyncio_mode = "auto"`).
- **LLM calls go through LangChain only** — never import `anthropic` for `messages.create`. The handler calls the shared `format_with_telemetry`; it never touches `ChatAnthropic` directly.
- **No new files except the test file.** No rename of `_oos_reply`. Reuse `_oos_reply`, `_OOS_REPLIES_BY_SUBREASON`, and the canned strings already in `general_chat_engine.py`.
- **No-regression invariant:** on any formatter failure, the reply must equal the canned line `_oos_reply(intent.raw)` would have produced.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/domains/general_chat/services/general_chat_engine.py` | Owns OOS/stock-advice canned content + resolver; now also the redirect handler | Add `should_tailor`, `format_redirect_or_canned`, `_REDIRECT_FORMATTER_BODY`, one runtime import (`format_with_telemetry`), a `TYPE_CHECKING` block |
| `app/domains/general_chat/tests/test_redirect.py` | Unit tests for the handler | **Create** |
| `app/domains/ai_engine/services/brain.py` | Chat-turn orchestrator | Classifier-only branch: compute reply via the handler (function-local import) |
| `app/domains/ai_engine/answer_formatter/formatter.py` | Shared formatter contract | *(Optional, Task 3)* add `"redirect"` to the `ActionMode` Literal |

**Behaviour matrix** (encoded in `should_tailor`):

| Intent | Sub-reason | Behaviour |
|---|---|---|
| `stock_advice` | — (`None`) | Tailor |
| `out_of_scope` | `off_topic`, `other` | Tailor |
| `out_of_scope` | `gibberish`, `identity_or_meta`, `security_or_credentials`, `chat_summary` | Canned, verbatim |

---

## Task 1: Redirect handler in `general_chat_engine.py`

**Files:**
- Create: `app/domains/general_chat/tests/test_redirect.py`
- Modify: `app/domains/general_chat/services/general_chat_engine.py`

**Interfaces:**
- Consumes (already present in the module): `_oos_reply(classification) -> str`, `_OOS_REPLIES_BY_SUBREASON: dict[OutOfScopeSubreason, str]`, `OUT_OF_SCOPE_MESSAGE: str`, `OutOfScopeSubreason` (enum), `ensure_ai_agents_path()` (called at import).
- Consumes (new import): `format_with_telemetry(*, ctx, facts_pack, body_prompt, module_name, action_mode, profile, build_fallback) -> str` from `app.domains.ai_engine.answer_formatter`.
- Produces:
  - `should_tailor(intent_name: str, subreason: OutOfScopeSubreason | None) -> bool`
  - `async format_redirect_or_canned(*, ctx: TurnContext, intent: IntentDecision) -> str` — `intent.name` is `"out_of_scope"` / `"stock_advice"`; `intent.raw` is the `ClassificationResult` (has `.out_of_scope_message`, `.out_of_scope_subreason`).

- [ ] **Step 1: Write the failing tests**

Create `app/domains/general_chat/tests/test_redirect.py`:

```python
"""Tests for the out_of_scope / stock_advice redirect handler.

should_tailor encodes the tailor-vs-canned matrix; format_redirect_or_canned
returns the sub-reason-specific canned line verbatim (no LLM) for the sensitive
sub-reasons, runs the shared formatter for the tailored ones, and falls back to
the canned line if the formatter fails.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_should_tailor_matrix():
    from app.domains.general_chat.services import general_chat_engine as eng
    S = eng.OutOfScopeSubreason
    assert eng.should_tailor("stock_advice", None) is True
    assert eng.should_tailor("out_of_scope", S.OFF_TOPIC) is True
    assert eng.should_tailor("out_of_scope", S.OTHER) is True
    for sr in (S.GIBBERISH, S.IDENTITY_OR_META, S.SECURITY_OR_CREDENTIALS, S.CHAT_SUMMARY):
        assert eng.should_tailor("out_of_scope", sr) is False


def test_canned_subreasons_return_specific_line_without_llm():
    from app.domains.general_chat.services import general_chat_engine as eng
    S = eng.OutOfScopeSubreason
    with patch.object(eng, "format_with_telemetry", new=AsyncMock()) as mock_fmt:
        for sr in (S.GIBBERISH, S.IDENTITY_OR_META, S.SECURITY_OR_CREDENTIALS, S.CHAT_SUMMARY):
            intent = SimpleNamespace(
                name="out_of_scope",
                raw=SimpleNamespace(
                    out_of_scope_message=eng.OUT_OF_SCOPE_MESSAGE,
                    out_of_scope_subreason=sr,
                ),
            )
            result = asyncio.run(eng.format_redirect_or_canned(ctx=None, intent=intent))
            assert result == eng._OOS_REPLIES_BY_SUBREASON[sr]
        mock_fmt.assert_not_called()


def test_tailored_calls_formatter_with_redirect_mode():
    from app.domains.general_chat.services import general_chat_engine as eng
    captured = {}

    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        captured.update(
            facts_pack=facts_pack, module_name=module_name,
            action_mode=action_mode, profile=profile,
        )
        return "TAILORED REPLY"

    intent = SimpleNamespace(
        name="out_of_scope",
        raw=SimpleNamespace(
            out_of_scope_message=eng.OUT_OF_SCOPE_MESSAGE,
            out_of_scope_subreason=eng.OutOfScopeSubreason.OFF_TOPIC,
        ),
    )
    ctx = SimpleNamespace(user_ctx=SimpleNamespace(first_name="Asha"))
    with patch.object(eng, "format_with_telemetry", new=fake_fmt):
        result = asyncio.run(eng.format_redirect_or_canned(ctx=ctx, intent=intent))
    assert result == "TAILORED REPLY"
    assert captured["action_mode"] == "redirect"
    assert captured["module_name"] == "out_of_scope"
    assert set(captured["facts_pack"]) == {"boundary_message"}
    assert captured["facts_pack"]["boundary_message"] == \
        eng._OOS_REPLIES_BY_SUBREASON[eng.OutOfScopeSubreason.OFF_TOPIC]
    assert captured["profile"] == {"first_name": "Asha"}


def test_tailored_falls_back_to_canned_on_formatter_failure():
    from app.domains.general_chat.services import general_chat_engine as eng

    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        return build_fallback()  # simulate FormatterFailure -> fallback closure

    intent = SimpleNamespace(
        name="stock_advice",
        raw=SimpleNamespace(out_of_scope_message="STOCK_CANNED", out_of_scope_subreason=None),
    )
    ctx = SimpleNamespace(user_ctx=SimpleNamespace(first_name=None))
    with patch.object(eng, "format_with_telemetry", new=fake_fmt):
        result = asyncio.run(eng.format_redirect_or_canned(ctx=ctx, intent=intent))
    assert result == "STOCK_CANNED"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/general_chat/tests/test_redirect.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'should_tailor'` (and `format_redirect_or_canned`).

- [ ] **Step 3: Add the `TYPE_CHECKING` block for annotations**

In `general_chat_engine.py`, immediately after the stdlib imports (`import json` / `import re`, around line 14), add:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext
    from app.domains.ai_engine.types import IntentDecision
```

(These are type-only — no runtime import, so no import cycle. `from __future__ import annotations` is already active at the top of the file.)

- [ ] **Step 4: Add the runtime import for the shared formatter**

In `general_chat_engine.py`, immediately after the existing block

```python
from app.domains.ai_engine.common import (
    build_history_block,
    ensure_ai_agents_path,
    format_inr_indian,
)
```

add:

```python
from app.domains.ai_engine.answer_formatter import format_with_telemetry
```

(`answer_formatter` self-injects its own `sys.path` for the persona import, so it is safe to import before the module's `ensure_ai_agents_path()` call. It does not import `general_chat`, so there is no cycle.)

- [ ] **Step 5: Add the handler code**

In `general_chat_engine.py`, directly after the `_oos_reply` function (after its `return OUT_OF_SCOPE_MESSAGE` line), add:

```python
_REDIRECT_FORMATTER_BODY = (
    "You are declining a request that falls outside what PI helps with, then "
    "redirecting the customer to what PI can do.\n"
    "\n"
    "FACTS_PACK has a single field, `boundary_message`: PI's authoritative "
    "statement of what it does and doesn't help with. Treat it as the source of "
    "truth for scope.\n"
    "\n"
    "Write the reply:\n"
    "- Open by briefly acknowledging, in your own words, what the customer "
    "actually asked — one short clause that shows you understood it.\n"
    "- Say plainly that it's outside what you can help with today.\n"
    "- Redirect to what PI does, drawing only on `boundary_message`. For the "
    "stock-advice case, convey its rationale: PI doesn't advise on individual "
    "stocks and instead focuses on a diversified, fund-based portfolio for "
    "long-term goals.\n"
    "\n"
    "Never do any of these:\n"
    "- Do not answer the out-of-scope request itself: no individual stock picks "
    "or buy/sell calls; no tax, insurance, legal, or medical advice; no help "
    "with passwords, logins, or credentials; no answering general-knowledge or "
    "off-topic questions.\n"
    "- Do not invent capabilities or scope beyond `boundary_message`.\n"
    "\n"
    "Keep it to 3-5 sentences, warm, in PI's voice."
)


def should_tailor(intent_name: str, subreason: OutOfScopeSubreason | None) -> bool:
    """True when the reply should be tailored by the formatter rather than
    returned as the verbatim canned line. Sensitive / contentless sub-reasons
    (gibberish, identity, security/credentials, chat-summary) stay canned."""
    if intent_name == "stock_advice":
        return True
    if intent_name == "out_of_scope":
        return subreason in {OutOfScopeSubreason.OFF_TOPIC, OutOfScopeSubreason.OTHER}
    return False


async def format_redirect_or_canned(*, ctx: "TurnContext", intent: "IntentDecision") -> str:
    """Reply for the classifier-only intents (out_of_scope / stock_advice).

    Resolves the canned line via ``_oos_reply``. For the tailored cases it runs
    the shared formatter to acknowledge the customer's question and redirect; on
    any formatter failure ``format_with_telemetry`` calls the fallback closure,
    which returns the same canned line (today's behaviour — zero regression).
    """
    resolved = _oos_reply(intent.raw)
    if not should_tailor(intent.name, intent.raw.out_of_scope_subreason):
        return resolved
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack={"boundary_message": resolved},
        body_prompt=_REDIRECT_FORMATTER_BODY,
        module_name=intent.name,
        action_mode="redirect",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: resolved,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/general_chat/tests/test_redirect.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Run the existing general_chat suite (no regression)**

Run: `cd Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/general_chat/tests/ -v`
Expected: PASS (existing `test_general_chat_engine.py` still green — the import addition didn't break it).

- [ ] **Step 8: Checkpoint (review gate)**

No git in this tree — pause here for review. Confirm: only `general_chat_engine.py` (additions) and the new `test_redirect.py` changed; `_oos_reply` and `_OOS_REPLIES_BY_SUBREASON` untouched.

---

## Task 2: Wire the brain short-circuit to the handler

**Files:**
- Modify: `app/domains/ai_engine/services/brain.py` (classifier-only branch, currently lines 130–141)

**Interfaces:**
- Consumes: `format_redirect_or_canned(*, ctx, intent) -> str` from Task 1.
- Produces: no new symbols. `ctx` (`TurnContext`) and `intent` (`IntentDecision`) already exist at this point in `run_turn`.

- [ ] **Step 1: Replace the canned return with the handler call**

In `brain.py`, change the classifier-only branch from:

```python
            # ---- 3. Classifier-only intents: surface the canned message -----
            if intent.name in _CLASSIFIER_ONLY_INTENTS and intent.raw is not None:
                canned = getattr(intent.raw, "out_of_scope_message", None)
                if canned:
                    return await self._finalize(
                        text=canned,
                        intent=intent,
                        flow=flow,
                        t0=t_all,
                        db=db,
                        uid=uid,
                        sid=sid,
                    )
```

to:

```python
            # ---- 3. Classifier-only intents: tailor the redirect, else canned -
            if intent.name in _CLASSIFIER_ONLY_INTENTS and intent.raw is not None:
                canned = getattr(intent.raw, "out_of_scope_message", None)
                if canned:
                    # Function-local import keeps brain free of module-level
                    # domain deps (its convention) and avoids any import cycle.
                    from app.domains.general_chat.services.general_chat_engine import (
                        format_redirect_or_canned,
                    )

                    text = await format_redirect_or_canned(ctx=ctx, intent=intent)
                    return await self._finalize(
                        text=text,
                        intent=intent,
                        flow=flow,
                        t0=t_all,
                        db=db,
                        uid=uid,
                        sid=sid,
                    )
```

The `canned = ...` guard is kept so that an (unexpected) missing canned message still falls through to flow dispatch exactly as today.

- [ ] **Step 2: Import smoke check (no cycle)**

Run: `cd Prozpr_Backend && .venv-mac/bin/python -c "import app.domains.ai_engine.services.brain; print('brain import OK')"`
Expected: prints `brain import OK` with no `ImportError`.

- [ ] **Step 3: Run the touched suites (no regression)**

Run: `cd Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/ai_engine/tests/ app/domains/general_chat/tests/ -v`
Expected: PASS (all green).

- [ ] **Step 4: Checkpoint (review gate)**

Pause for review. Confirm the only change in `brain.py` is the two added lines (local import + `text = await ...`) plus the comment, and the `_finalize` call now passes `text=text`.

---

## Task 3 *(Optional, documentation-only)*: Add `"redirect"` to the `ActionMode` Literal

Skip this task entirely if you prefer `formatter.py` stays untouched — the feature is identical either way. Do it to keep the Literal/comment consistent with the new `action_mode="redirect"` usage.

**Files:**
- Modify: `app/domains/ai_engine/answer_formatter/formatter.py` (lines 37–45)

- [ ] **Step 1: Update the Literal and its comment**

Change:

```python
# Modes that pass through the formatter. clarify / redirect bypass it.
ActionMode = Literal[
    "compute",
    "narrate",
    "educate",
    "recompute",  # rebalancing
    "recompute_full",  # asset_allocation
    "counterfactual_explore",  # both
]
```

to:

```python
# Modes that pass through the formatter. clarify bypasses it.
ActionMode = Literal[
    "compute",
    "narrate",
    "educate",
    "recompute",  # rebalancing
    "recompute_full",  # asset_allocation
    "counterfactual_explore",  # both
    "redirect",  # out_of_scope / stock_advice
]
```

- [ ] **Step 2: Run the formatter tests**

Run: `cd Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/ai_engine/answer_formatter/tests/ -v`
Expected: PASS.

- [ ] **Step 3: Checkpoint (review gate)**

Pause for review. The only change is the Literal member + comment wording; no behaviour change.

---

## Manual / eval verification (after Tasks 1–2)

The "redirects without answering" quality is LLM behaviour and is verified by judgment, not the deterministic suite. With valid Anthropic keys, run these messages through chat (or the eval harness) and confirm each acknowledges the question and redirects **without** answering the topic:

- "Which stock should I buy right now?" → no ticker / buy-sell call; redirects to portfolio/allocation help.
- "How do I file my income tax?" → no tax instructions.
- "What's the weather today?" / "Tell me a joke." → acknowledges, redirects, no answer.
- "I forgot my password, can you reset it?" → the exact `security_or_credentials` canned line (deterministic; no LLM).

---

## Self-Review

**Spec coverage:**
- Tailor `off_topic` / `other` / `stock_advice`, keep the 4 sensitive sub-reasons canned → `should_tailor` + Task 1 tests ✓
- Activate sub-reason-specific canned lines → falls out of `_oos_reply` reuse; `test_canned_subreasons_return_specific_line_without_llm` locks it ✓
- Facts pack = single `boundary_message` key → `test_tailored_calls_formatter_with_redirect_mode` asserts key set ✓
- No-regression on formatter failure → `test_tailored_falls_back_to_canned_on_formatter_failure` ✓
- Brain wiring via the handler → Task 2 + import smoke check ✓
- Optional `ActionMode` Literal → Task 3 ✓

**Placeholder scan:** none — every code/test/command step has concrete content.

**Type/name consistency:** `should_tailor` and `format_redirect_or_canned` signatures match between Task 1 (definition), the Task 1 tests, and Task 2 (call site). `format_with_telemetry` keyword args match the real signature in `answer_formatter/formatter.py`.
