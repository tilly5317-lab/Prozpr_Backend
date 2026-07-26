# Reasoning / Answer Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three raw free-text customer-facing surfaces (`answer_formatter`, market-commentary QA, market-commentary doc-gen) emit through a forced tool whose first, required field is a *discarded* reasoning scratchpad — so working-out, raw scores, and preamble stay out of the rendered reply.

**Architecture:** One shared stdlib helper `AI_Agents/src/reasoned_reply.py` builds an Anthropic tool schema with the thinking field declared first and extracts the answer (discarding the thinking field). Each surface keeps its existing system prompt and public `str` return; only the output *mechanism* changes from free-text to forced `tool_choice`, mirroring the proven `general_chat` pattern.

**Tech Stack:** Python 3, `langchain-anthropic` (`ChatAnthropic.bind_tools(..., tool_choice=...)`), pytest (`.venv-mac/bin/python -m pytest`), existing `chat_eval` harness.

> **Commit policy for this plan:** per the user's standing rule, **do not run `git commit` until the user asks.** The `- [ ] Commit` steps below mark the intended commit boundaries; treat them as "stage + checkpoint for review" and hold the actual commit until the user gives the go-ahead. Nothing on `Chat_improvements` is committed yet.

> **Cost note:** Tasks 1, 7, 8 invoke the real Anthropic API (money). Task 1 reuses an existing snapshot (no new spend). Tasks 7–8 need the backend + dev Postgres reachable and the user's go-ahead before running.

---

## File Structure

- **Create:** `AI_Agents/src/reasoned_reply.py` — shared "reasoning-first, discarded" tool builder + extractor. Stdlib-only; no peer-agent imports.
- **Create:** `AI_Agents/src/test_reasoned_reply.py` — unit tests for the helper.
- **Modify:** `AI_Agents/src/market_commentary/chat_qa.py` — forced-tool QA.
- **Create:** `AI_Agents/src/market_commentary/test_chat_qa_reasoning.py` — discard test.
- **Modify:** `AI_Agents/src/market_commentary/document_generator.py` — forced-tool doc-gen (discarded field = `outline`).
- **Create:** `AI_Agents/src/market_commentary/test_document_generator_reasoning.py` — discard test.
- **Modify:** `app/domains/ai_engine/answer_formatter/formatter.py` — `_invoke_llm` forced-tool + module-top helper import + `max_tokens` 2000→2600.
- **Modify:** `app/domains/ai_engine/answer_formatter/tests/test_formatter.py` — update the truncation test to the tool-call shape; add a reasoning-discard test.
- **Modify:** `AI_Agents/src/chat_eval/build_diff_html.py` — extend `_flags` with raw-score + preamble regexes.
- **Create:** `AI_Agents/src/chat_eval/test_diff_flags.py` — flag unit tests.
- **Create (optional, Task 7):** `AI_Agents/src/chat_eval/reasoning_surface_snapshot.py` — targeted before/after capture for QA + doc-gen.

---

## Task 1: Freeze the baseline (no new API spend)

**Files:**
- Create: `AI_Agents/src/chat_eval/snapshots/pre-reasoning-2026-06-15.json`

The most recent chat snapshot `after-2026-06-14.json` (Jun 14 23:52) is the current code's output — **no code under eval has changed since** (only spec/plan docs added). So it IS the pre-reasoning baseline; copy it rather than spending API budget on a fresh run.

- [ ] **Step 1: Copy the latest snapshot as the labeled baseline**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
cp AI_Agents/src/chat_eval/snapshots/after-2026-06-14.json \
   AI_Agents/src/chat_eval/snapshots/pre-reasoning-2026-06-15.json
```

- [ ] **Step 2: Verify it has rows**

```bash
.venv-mac/bin/python -c "import json,pathlib; d=json.loads(pathlib.Path('AI_Agents/src/chat_eval/snapshots/pre-reasoning-2026-06-15.json').read_text()); print('rows:', len(d['rows']))"
```
Expected: `rows: 16`

> **Precondition check:** if any chat-facing code (formatter / engines / persona) HAS changed since 2026-06-14, this reuse is invalid — instead run a fresh baseline: `.venv-mac/bin/python AI_Agents/src/chat_eval/run_eval.py --questions AI_Agents/src/chat_eval/questions_voice.yaml --label pre-reasoning-2026-06-15 --email amoulsinghi08@gmail.com` (real API spend; needs backend + Postgres).

---

## Task 2: Shared helper `reasoned_reply.py`

**Files:**
- Create: `AI_Agents/src/reasoned_reply.py`
- Test: `AI_Agents/src/test_reasoned_reply.py`

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/test_reasoned_reply.py
"""Unit tests for the shared reasoning-first reply contract."""
from reasoned_reply import reasoned_reply_tool, extract_reasoned_reply


def test_thinking_field_declared_first_and_required():
    tool = reasoned_reply_tool(name="t", answer_description="the answer")
    props = list(tool["input_schema"]["properties"].keys())
    assert props[0] == "reasoning"          # first → conditions the answer tokens
    assert props[1] == "answer"
    assert tool["input_schema"]["required"] == ["reasoning", "answer"]


def test_custom_field_names_preserve_thinking_first():
    tool = reasoned_reply_tool(
        name="t", answer_field="document", answer_description="d",
        thinking_field="outline", thinking_description="plan here",
    )
    assert list(tool["input_schema"]["properties"].keys()) == ["outline", "document"]
    assert tool["input_schema"]["required"] == ["outline", "document"]


class _Resp:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


def test_extract_returns_answer_and_discards_thinking():
    r = _Resp([{"name": "t", "args": {"reasoning": "SECRET", "answer": "Clean."}}])
    assert extract_reasoned_reply(r) == "Clean."


def test_extract_returns_none_on_missing_empty_or_malformed():
    assert extract_reasoned_reply(_Resp([])) is None
    assert extract_reasoned_reply(_Resp([{"name": "t", "args": {"answer": "  "}}])) is None
    assert extract_reasoned_reply(_Resp([{"name": "t", "args": {}}])) is None
    assert extract_reasoned_reply(_Resp(None)) is None


def test_extract_honours_custom_answer_field():
    r = _Resp([{"name": "t", "args": {"outline": "x", "document": "# Doc"}}])
    assert extract_reasoned_reply(r, answer_field="document") == "# Doc"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/test_reasoned_reply.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reasoned_reply'`

- [ ] **Step 3: Implement the helper**

```python
# AI_Agents/src/reasoned_reply.py
"""Shared 'reasoning-first, discarded' tool contract for free-text surfaces.

A surface that should think privately before answering binds the tool from
``reasoned_reply_tool`` with forced ``tool_choice`` and reads the answer back with
``extract_reasoned_reply``. The thinking field is declared FIRST so its tokens are
generated before — and therefore condition — the answer tokens, and the backend never
returns it to the customer.

Self-contained (stdlib only); must not import any peer agent module. Duck-types the
response's ``.tool_calls`` so it does not depend on langchain.
"""
from __future__ import annotations

DEFAULT_REASONING_DESC = (
    "Your private scratchpad. Think through the answer here — which figures apply, which "
    "named band a score maps to, whether to include market context, what to leave out. The "
    "customer NEVER sees this field; it is discarded. Do all working-out here so the answer "
    "field stays clean (no preamble, no raw scores, no internal field names)."
)


def reasoned_reply_tool(
    *,
    name: str,
    answer_description: str,
    answer_field: str = "answer",
    thinking_field: str = "reasoning",
    thinking_description: str = DEFAULT_REASONING_DESC,
) -> dict:
    """Build an Anthropic tool whose input_schema declares ``thinking_field`` FIRST then
    ``answer_field``, both required. Field order is load-bearing — do not reorder."""
    return {
        "name": name,
        "description": (
            "Return the final customer-facing reply. Call this exactly once. Put your "
            "private working-out in the thinking field (discarded) and the clean, "
            "customer-ready text in the answer field. Emit no free-text outside this call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                thinking_field: {"type": "string", "description": thinking_description},
                answer_field: {"type": "string", "description": answer_description},
            },
            "required": [thinking_field, answer_field],
        },
    }


def extract_reasoned_reply(response, *, answer_field: str = "answer") -> str | None:
    """Return the answer field from a forced-tool response, or None if the tool call is
    missing/empty/malformed (caller falls back). The thinking field is never returned."""
    tool_calls = getattr(response, "tool_calls", None) or []
    for call in tool_calls:
        args = (call.get("args") if isinstance(call, dict) else None) or {}
        value = args.get(answer_field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/test_reasoned_reply.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit** (hold per commit policy)

```bash
git add AI_Agents/src/reasoned_reply.py AI_Agents/src/test_reasoned_reply.py
git commit -m "feat(persona): add shared reasoning-first reply tool helper"
```

---

## Task 3: Migrate market-commentary QA (`chat_qa.py`)

**Files:**
- Modify: `AI_Agents/src/market_commentary/chat_qa.py`
- Test: `AI_Agents/src/market_commentary/test_chat_qa_reasoning.py`

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/market_commentary/test_chat_qa_reasoning.py
"""The QA surface must emit through the forced tool and discard the reasoning field."""
from unittest.mock import MagicMock, patch

import market_commentary.chat_qa as qa


def test_answer_question_returns_answer_and_discards_reasoning():
    resp = MagicMock()
    resp.tool_calls = [{
        "name": "return_qa_answer",
        "args": {"reasoning": "SECRET WORKING OUT", "answer": "The repo rate is 5.25%."},
    }]
    with patch.object(qa, "_qa_llm_bound") as bound:
        bound.invoke.return_value = resp
        out = qa.answer_question("what's the repo rate?", document_content="... repo 5.25% ...")
    assert out == "The repo rate is 5.25%."
    assert "SECRET" not in out


def test_answer_question_falls_back_on_malformed_tool_call():
    resp = MagicMock()
    resp.tool_calls = []
    with patch.object(qa, "_qa_llm_bound") as bound:
        bound.invoke.return_value = resp
        out = qa.answer_question("?", document_content="doc")
    assert isinstance(out, str) and out.strip()   # safe fallback, no crash
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/market_commentary/test_chat_qa_reasoning.py -v`
Expected: FAIL — `AttributeError: module 'market_commentary.chat_qa' has no attribute '_qa_llm_bound'`

- [ ] **Step 3: Convert the surface**

Replace the chain (current `chat_qa.py:6-7,20-21,62-65`). New top-of-file imports and binding:

```python
# chat_qa.py — replace `from langchain_core.output_parsers import StrOutputParser`
from reasoned_reply import reasoned_reply_tool, extract_reasoned_reply

# ... keep _QA_MODEL / _QA_MAX_TOKENS, bump tokens for the reasoning field:
_QA_MAX_TOKENS = 1500          # was 1024 — room for the discarded reasoning field

_qa_llm = ChatAnthropic(model=_QA_MODEL, max_tokens=_QA_MAX_TOKENS)

_QA_TOOL = reasoned_reply_tool(
    name="return_qa_answer",
    answer_description=(
        "The clean, customer-facing answer grounded ONLY in the market-commentary "
        "document. 2-5 short sentences. No preamble, no working-out."
    ),
)
_qa_llm_bound = _qa_llm.bind_tools(
    [_QA_TOOL], tool_choice={"type": "tool", "name": "return_qa_answer"}
)
```

Replace the body of `answer_question` (the final `return qa_chain.invoke({...})`) with:

```python
    messages = QA_PROMPT.format_messages(
        document_content=document_content, user_question=user_question,
    )
    answer = extract_reasoned_reply(_qa_llm_bound.invoke(messages))
    if answer:
        return answer
    # Rare malformed tool call — return a safe, on-voice fallback rather than crash.
    return "I couldn't find that in the latest market commentary — could you rephrase?"
```

Delete the now-unused `qa_chain = QA_PROMPT | _qa_llm | StrOutputParser()` line and the `StrOutputParser` import (orphaned by this change).

- [ ] **Step 4: Run tests to confirm they pass**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/market_commentary/test_chat_qa_reasoning.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit** (hold per commit policy)

```bash
git add AI_Agents/src/market_commentary/chat_qa.py AI_Agents/src/market_commentary/test_chat_qa_reasoning.py
git commit -m "feat(market-commentary): QA answers via forced tool with discarded reasoning"
```

---

## Task 4: Migrate market-commentary doc-gen (`document_generator.py`)

**Files:**
- Modify: `AI_Agents/src/market_commentary/document_generator.py`
- Test: `AI_Agents/src/market_commentary/test_document_generator_reasoning.py`

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/market_commentary/test_document_generator_reasoning.py
"""Doc-gen must emit through the forced tool and discard the `outline` field."""
from unittest.mock import MagicMock, patch

import market_commentary.document_generator as dg


def test_generate_document_returns_document_and_discards_outline():
    resp = MagicMock()
    resp.tool_calls = [{
        "name": "return_commentary_document",
        "args": {"outline": "SECRET PLAN", "document": "# Prozpr\nMarket Commentary..."},
    }]
    # Isolate the extract+discard behavior from prompt/snapshot plumbing.
    with patch.object(dg, "_build_prompt_vars", return_value={}), \
         patch.object(dg.DOCUMENT_GENERATION_PROMPT, "format_messages", return_value=["m"]), \
         patch.object(dg, "_llm_bound") as bound:
        bound.invoke.return_value = resp
        out = dg.generate_document(snapshot=None)
    assert out.startswith("# Prozpr")
    assert "SECRET PLAN" not in out
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/market_commentary/test_document_generator_reasoning.py -v`
Expected: FAIL — `AttributeError: module 'market_commentary.document_generator' has no attribute '_llm_bound'`

- [ ] **Step 3: Convert the surface**

In `document_generator.py`: bump tokens, add the helper import, replace the LCEL chain (current lines 6-8,75-87) with a bound LLM + explicit `generate_document` body.

```python
# replace the StrOutputParser / RunnableLambda imports with:
from reasoned_reply import reasoned_reply_tool, extract_reasoned_reply

_MAX_TOKENS = 3800  # was 3072 — 2-page doc + a short discarded outline

_llm = ChatAnthropic(model=_DOCUMENT_MODEL, max_tokens=_MAX_TOKENS)

_DOC_TOOL = reasoned_reply_tool(
    name="return_commentary_document",
    answer_field="document",
    answer_description=(
        "The complete 2-page Markdown commentary, following the required structure, "
        "letterhead, and disclaimer exactly. Begin at the letterhead — no preamble."
    ),
    thinking_field="outline",
    thinking_description=(
        "Private planning scratchpad (discarded, max a few lines): jot the section order "
        "and which macro figures land in each section before writing. Never shown."
    ),
)
_llm_bound = _llm.bind_tools(
    [_DOC_TOOL], tool_choice={"type": "tool", "name": "return_commentary_document"}
)


def generate_document(snapshot: "MacroSnapshot", date: "Optional[datetime]" = None) -> str:
    """Generate a 2-page Markdown market commentary from a MacroSnapshot."""
    prompt_vars = _build_prompt_vars({"snapshot": snapshot, "date": date})
    messages = DOCUMENT_GENERATION_PROMPT.format_messages(**prompt_vars)
    document = extract_reasoned_reply(_llm_bound.invoke(messages), answer_field="document")
    if not document:
        raise RuntimeError("Document generation returned no `document` field.")
    return document
```

Delete the old `document_generation_chain = RunnableLambda(...) | ... | StrOutputParser()` and the now-unused `StrOutputParser` / `RunnableLambda` imports. Keep `_build_prompt_vars`, `_fmt`, `_spread`, and the `DocumentGenerator` wrapper (it already delegates to `generate_document`).

- [ ] **Step 4: Run tests to confirm they pass**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/market_commentary/test_document_generator_reasoning.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit** (hold per commit policy)

```bash
git add AI_Agents/src/market_commentary/document_generator.py AI_Agents/src/market_commentary/test_document_generator_reasoning.py
git commit -m "feat(market-commentary): doc-gen via forced tool with discarded outline"
```

---

## Task 5: Migrate `answer_formatter` (highest value + highest care)

**Files:**
- Modify: `app/domains/ai_engine/answer_formatter/formatter.py` (`_invoke_llm`, formatter.py:151-176; helper import near formatter.py:24)
- Test: `app/domains/ai_engine/answer_formatter/tests/test_formatter.py`

- [ ] **Step 1: Update the truncation test + add the discard test**

In `test_formatter.py`, **replace** `test_invoke_llm_raises_formatter_failure_when_response_truncated` (currently builds a `_FakeLLM` with `.invoke` returning free-text `.content`) with the tool-call shape, and **add** a discard test:

```python
def test_invoke_llm_raises_formatter_failure_when_response_truncated():
    """Hitting max_tokens must still raise FormatterFailure so the bridge falls back."""
    from app.domains.ai_engine.answer_formatter import formatter as fmt

    class _FakeMessage:
        tool_calls = []                                   # truncated mid tool-call
        response_metadata = {"stop_reason": "max_tokens"}

    class _BoundLLM:
        def invoke(self, _msgs):
            return _FakeMessage()

    class _FakeLLM:
        def __init__(self, **_kw):
            pass
        def bind_tools(self, *_a, **_kw):
            return _BoundLLM()

    with patch("langchain_anthropic.ChatAnthropic", _FakeLLM), \
         patch("app.core.config.get_settings") as gs:
        gs.return_value.get_anthropic_answer_formatter_key.return_value = "sk-test"
        with pytest.raises(FormatterFailure, match="truncated"):
            asyncio.run(fmt._invoke_llm("sys", "user"))


def test_invoke_llm_returns_answer_and_discards_reasoning():
    """The reasoning field is internal; only `answer` reaches the customer."""
    from app.domains.ai_engine.answer_formatter import formatter as fmt

    class _FakeMessage:
        tool_calls = [{
            "name": "return_formatted_answer",
            "args": {"reasoning": "SECRET: risk 9.1 -> Aggressive band",
                     "answer": "You're an **Aggressive** investor."},
        }]
        response_metadata = {"stop_reason": "tool_use"}

    class _BoundLLM:
        def invoke(self, _msgs):
            return _FakeMessage()

    class _FakeLLM:
        def __init__(self, **_kw):
            pass
        def bind_tools(self, *_a, **_kw):
            return _BoundLLM()

    with patch("langchain_anthropic.ChatAnthropic", _FakeLLM), \
         patch("app.core.config.get_settings") as gs:
        gs.return_value.get_anthropic_answer_formatter_key.return_value = "sk-test"
        out = asyncio.run(fmt._invoke_llm("sys", "user"))
    assert out == "You're an **Aggressive** investor."
    assert "SECRET" not in out
```

- [ ] **Step 2: Run to confirm the new tests fail**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/answer_formatter/tests/test_formatter.py -v`
Expected: the two tests above FAIL (current `_invoke_llm` reads `.content`, has no `bind_tools` path).

- [ ] **Step 3: Add the helper import (module top, next to persona)**

After `from persona import build_system_prompt  # noqa: E402` (formatter.py:24), add:

```python
from reasoned_reply import reasoned_reply_tool, extract_reasoned_reply  # noqa: E402
```

- [ ] **Step 4: Rewrite `_invoke_llm` (formatter.py:151-176)**

```python
async def _invoke_llm(system_text: str, user_text: str) -> str:
    """Single Haiku 4.5 call via a forced tool; the reasoning field is discarded.
    Isolated so tests can patch it (and ChatAnthropic beneath it)."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.core.config import get_settings

    api_key = get_settings().get_anthropic_answer_formatter_key()
    tool = reasoned_reply_tool(
        name="return_formatted_answer",
        answer_description=(
            "The clean, customer-facing answer in PI's voice and the required markdown "
            "format. No preamble, no working-out, no internal field names or raw N/10 scores."
        ),
    )
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=2600,                       # was 2000 — room for the discarded reasoning
    ).bind_tools([tool], tool_choice={"type": "tool", "name": "return_formatted_answer"})
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    stop_reason = getattr(raw, "response_metadata", {}).get("stop_reason")
    if stop_reason == "max_tokens":
        # Mid-response truncation looks worse than the deterministic fallback brief.
        raise FormatterFailure("formatter_truncated_at_max_tokens")
    answer = extract_reasoned_reply(raw)
    if not answer:
        raise FormatterFailure("formatter_no_tool_call")
    return answer
```

- [ ] **Step 5: Run the full formatter test module**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/answer_formatter/tests/test_formatter.py -v`
Expected: all pass. Note `test_format_answer_returns_text_on_success` (patches `_invoke_llm` directly) and the telemetry tests stay green — the public `format_answer -> str` contract is unchanged.

- [ ] **Step 6: Commit** (hold per commit policy)

```bash
git add app/domains/ai_engine/answer_formatter/formatter.py app/domains/ai_engine/answer_formatter/tests/test_formatter.py
git commit -m "feat(answer-formatter): emit via forced tool, discard reasoning scratchpad"
```

---

## Task 6: Leakage flags in the diff renderer

**Files:**
- Modify: `AI_Agents/src/chat_eval/build_diff_html.py` (`_flags`, lines 17-24)
- Test: `AI_Agents/src/chat_eval/test_diff_flags.py`

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/chat_eval/test_diff_flags.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))   # chat_eval/ for build_diff_html + build_html
from build_diff_html import _flags


def test_flags_detect_raw_score_and_preamble():
    assert "⚠ raw score /10" in _flags("Your risk score is 9.1/10.")
    assert any("preamble" in f for f in _flags("Here's what the numbers show: ..."))
    assert any("preamble" in f for f in _flags("Looking at your current financial plan, ..."))


def test_clean_answer_has_no_flags():
    assert _flags("You're an Aggressive investor with a balanced equity-debt mix.") == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/chat_eval/test_diff_flags.py -v`
Expected: FAIL on the raw-score / preamble assertions.

- [ ] **Step 3: Extend `_flags`**

```python
def _flags(text: str) -> list[str]:
    t = text or ""
    out = []
    if re.search(r"(?<!\w)Tilly(?!\w)", t, re.I):
        out.append("⚠ says Tilly")
    if re.search(r"(?<!\w)(million|billion)(?!\w)", t, re.I):
        out.append("⚠ million/billion")
    if re.search(r"\b\d+(?:\.\d+)?\s*/\s*10\b", t):
        out.append("⚠ raw score /10")
    if re.search(r"\b(here's what|looking at your|based on the data|let me)\b", t, re.I):
        out.append("⚠ preamble/working-out")
    return out
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/chat_eval/test_diff_flags.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit** (hold per commit policy)

```bash
git add AI_Agents/src/chat_eval/build_diff_html.py AI_Agents/src/chat_eval/test_diff_flags.py
git commit -m "feat(chat-eval): flag raw scores and working-out preamble in the diff"
```

---

## Task 7 (OPTIONAL — low-traffic surfaces): targeted before/after for QA + doc-gen

QA and doc-gen are **not** reachable through `ChatBrain.run_turn`, so the chat eval won't show them. The unit tests (Tasks 3-4) already prove reasoning/outline is discarded; this task adds a human-readable voice before/after. Optional because both are low-traffic and need real API calls. **Capture the "before" leg BEFORE Tasks 3-4 if you want a true diff** (the helper script is a pure addition, so it can be written first).

**Files:**
- Create: `AI_Agents/src/chat_eval/reasoning_surface_snapshot.py`

- [ ] **Step 1: Write the capture script** (committed fixture doc + a real `MacroSnapshot`)

```python
# AI_Agents/src/chat_eval/reasoning_surface_snapshot.py
"""Capture QA + doc-gen output for human before/after voice review.
Usage: .venv-mac/bin/python AI_Agents/src/chat_eval/reasoning_surface_snapshot.py --label before
Writes snapshots/reasoning_surfaces_<label>.json. Real API spend.
"""
import argparse, json
from pathlib import Path

from market_commentary.chat_qa import answer_question
from market_commentary.document_generator import generate_document
from market_commentary.models import MacroSnapshot

HERE = Path(__file__).parent
_DOC = (HERE / "snapshots" / "qa_fixture_doc.md")
FIXTURE_DOC = _DOC.read_text(encoding="utf-8") if _DOC.exists() else (
    "# Prozpr Market Commentary | June 2026\nRBI repo rate 5.25% (neutral). "
    "Nifty 50 PE 22.4x (fair). 10-yr G-Sec 6.8%. Brent crude $82/bbl. USD/INR 85.6."
)
QUESTIONS = ["What's the RBI repo rate?", "Are mid-caps expensive right now?",
             "How might crude oil prices affect Indian inflation?"]
SNAP = MacroSnapshot(
    repo_rate_pct=5.25, rbi_stance="neutral", cpi_yoy_pct=4.1, nifty50_pe=22.4,
    nifty_midcap150_pe=34.0, nifty_smallcap250_pe=28.0, gsec_10yr_yield_pct=6.8,
    sbi_fd_1yr_rate_pct=6.5, gold_price_inr_per_10g=78000.0, gold_price_usd_per_oz=2400.0,
    fed_funds_rate_pct=4.5, fii_net_flows_cr_inr=-3200.0, brent_crude_usd=82.0,
    usd_inr_rate=85.6, data_gaps=[],
)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--label", required=True)
    label = ap.parse_args().label
    out = {"qa": [{"q": q, "a": answer_question(q, document_content=FIXTURE_DOC)} for q in QUESTIONS],
           "document": generate_document(SNAP)}
    p = HERE / "snapshots" / f"reasoning_surfaces_{label}.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {p}")


if __name__ == "__main__":
    main()
```

> Verify `MacroSnapshot`'s constructor field names against `AI_Agents/src/market_commentary/models.py` before running; adjust the kwargs if they differ.

- [ ] **Step 2: Capture before (only meaningful if run before Tasks 3-4)**

Run: `.venv-mac/bin/python AI_Agents/src/chat_eval/reasoning_surface_snapshot.py --label before`

- [ ] **Step 3: After Tasks 3-4, capture after + eyeball**

Run: `.venv-mac/bin/python AI_Agents/src/chat_eval/reasoning_surface_snapshot.py --label after`
Compare `reasoning_surfaces_before.json` vs `reasoning_surfaces_after.json`: answers should be clean (no preamble), document should start at the letterhead.

- [ ] **Step 4: Commit** (hold per commit policy)

```bash
git add AI_Agents/src/chat_eval/reasoning_surface_snapshot.py
git commit -m "test(chat-eval): targeted before/after capture for QA and doc-gen"
```

---

## Task 8: Re-run the chat eval, render the before/after, review

**Files:** none (verification). Real API spend; needs backend + dev Postgres reachable.

- [ ] **Step 1: Re-run the 16-question voice eval against the real profile**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
.venv-mac/bin/python AI_Agents/src/chat_eval/run_eval.py \
  --questions AI_Agents/src/chat_eval/questions_voice.yaml \
  --label after-reasoning-2026-06-15 \
  --email amoulsinghi08@gmail.com
```

- [ ] **Step 2: Render the two-column before/after**

```bash
.venv-mac/bin/python AI_Agents/src/chat_eval/build_diff_html.py \
  --before snapshots/pre-reasoning-2026-06-15.json \
  --after  snapshots/after-reasoning-2026-06-15.json
```

- [ ] **Step 3: Review `diff.html`**

Pass conditions: the `answer_formatter`-driven rows (`v_aa_plan`, `v_aa_why`, `v_gp_feasible`, `v_aa_intl`, `v_gp_retire`, `v_aa_emergency`, `v_rebal`) no longer show the `⚠ raw score /10` or `⚠ preamble/working-out` flags, answers read as clean PI voice, and no row regressed (no `Tilly` / `million/billion`). `v_rebal` may still show the known eval-session-FK "technical issue" artifact (not a product bug).

- [ ] **Step 4: Run the targeted unit-test sweep (no network)**

```bash
.venv-mac/bin/python -m pytest \
  AI_Agents/src/test_reasoned_reply.py \
  AI_Agents/src/market_commentary/test_chat_qa_reasoning.py \
  AI_Agents/src/market_commentary/test_document_generator_reasoning.py \
  AI_Agents/src/chat_eval/test_diff_flags.py \
  app/domains/ai_engine/answer_formatter/tests/test_formatter.py -v
```
Expected: all pass.

---

## Done criteria

- `reasoned_reply.py` exists with the thinking-first contract + tests green.
- All three free-text surfaces emit via forced `tool_choice`, return a `str`, and discard the thinking field (unit-proven).
- `answer_formatter` keeps its `str` contract, cache_control, and truncation→fallback guard.
- The before/after diff shows the raw-score and preamble leaks gone on the answer_formatter rows, no regressions.
- (Optional) QA + doc-gen before/after captured and eyeballed.
