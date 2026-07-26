# Reasoning / Answer Separation — Design Spec

- **Date:** 2026-06-15
- **Status:** Draft for review
- **Owner:** Ask PI backend
- **Builds on:** `2026-06-14-persona-consolidation-design.md` (this is technique #3 of the original audit; #4 shipped in persona consolidation)

## Problem

Customer-facing answers should contain the *answer*, not the model's working-out. Today the
"think internally, emit only the clean reply" pattern is implemented unevenly:

- **6 of 9 LLM surfaces already separate cleanly** via forced structured output (the model's reasoning
  never reaches a free-text field the customer sees): `general_chat` (two-pass research→compose, forced
  `return_reply`), `portfolio_query`, the AA composer, AA rationale, risk profiling, cashflow summarizer.
- **3 surfaces are raw free-text** — the model's prose *is* the output, so working-out and meta-commentary
  leak into the customer's tone:
  - `app/domains/ai_engine/answer_formatter/formatter.py` — `_invoke_llm` (formatter.py:151) returns
    `raw.content` directly. **This is the main renderer for AA / rebalancing / goal-planning chat —
    highest traffic, zero separation.**
  - `AI_Agents/src/market_commentary/chat_qa.py:21` — `QA_PROMPT | llm | StrOutputParser()` (Sonnet).
  - `AI_Agents/src/market_commentary/document_generator.py:77` — `... | llm | StrOutputParser()` (Haiku).

**Evidence this is real, not preventative** — from the persona-consolidation before/after eval snapshots:
- raw internal value surfaced: *"Your risk score is **9.1/10**"* (should lead with the named band, not the
  raw score) — answer_formatter / AA path.
- working-out framing bleeding into tone: *"Looking at your current financial plan…"*, *"Here's what the
  numbers show…"*.

## Goals

1. Make the three free-text surfaces **separate reasoning from the customer-facing answer**, so working-out,
   internal field values, and preamble stay out of the rendered reply.
2. Do it with a **single LLM call per surface** (no extra latency / round-trip) — the working-out lands in a
   schema field the backend discards.
3. **Measure** the change with the existing before/after eval harness (technique #5), so we can see the leaks
   disappear rather than eyeballing one answer.

## Non-goals (surgical scope — confirmed "all free-text surfaces", not "every chat surface")

- **Do not touch** the 6 surfaces that already separate (`general_chat`, `portfolio_query`, AA composer, AA
  rationale, risk, cashflow). Adding a reasoning field to surfaces that are already clean only spends tokens.
- No native extended-thinking (`thinking={"type":"enabled"}`): it conflicts with the `temperature=0` +
  forced-`tool_choice` setups several surfaces use, and the schema-scratchpad gets us the same separation
  more cheaply. Out of scope.
- No change to routing, intent classification, or the numeric engines.
- No tone/prompt-content rewrites beyond what the mechanism requires (e.g. we keep each surface's existing
  body/persona; we only change *how* the output is emitted).

## The mechanism: a discarded reasoning field, declared first

Each surface stops emitting free-text and instead is **forced to call one tool** whose schema declares a
**`reasoning` field FIRST (required)**, then the customer-facing field:

```
return_reasoned_reply(
    reasoning: str   # REQUIRED, declared first. The model works out HERE: which risk band a
                     # score maps to, whether market commentary is even relevant, how to phrase
                     # a number, what NOT to say. The backend DISCARDS this field.
    answer: str      # the only field rendered to the customer
)
```

**Why field order is the crux:** the model generates tool-argument tokens in the order the schema declares
them, so `reasoning` tokens are produced before — and therefore condition — the `answer` tokens. It is
chain-of-thought whose "chain" lands in a field we throw away instead of in the customer's chat bubble.
This is a single call (no second LLM round-trip; `general_chat`'s two-pass exists only because pass 1 needs
`web_search`, which these surfaces do not). Forced `tool_choice` is already the idiomatic output mechanism in
this codebase (`general_chat`, `portfolio_query`, AA composer), so this fits existing patterns and the
existing fallback-on-malformed-tool-call handling.

## Architecture

### New shared helper: `AI_Agents/src/reasoned_reply.py`

Single source of truth for the "reasoning-first, discarded" contract. Self-contained, stdlib-only (same
constraint as `persona.py` / `common.py`); must not import any peer agent module. Duck-types the response
object's `.tool_calls` so it does not import langchain.

```python
# AI_Agents/src/reasoned_reply.py
DEFAULT_REASONING_DESC = (
    "Your private scratchpad. Think through the answer here — which figures apply, which named band a "
    "score maps to, whether to include market context, what to leave out. The customer NEVER sees this "
    "field; it is discarded. Do all working-out here so the answer field stays clean."
)

def reasoned_reply_tool(
    *,
    name: str,                       # e.g. "return_reasoned_reply"
    answer_field: str = "answer",    # "answer" | "document"
    answer_description: str,
    thinking_field: str = "reasoning",   # "reasoning" | "outline"
    thinking_description: str = DEFAULT_REASONING_DESC,
) -> dict:
    """Return an Anthropic tool dict whose input_schema lists `thinking_field` FIRST then
    `answer_field`, with both required. Field order is load-bearing — do not reorder."""

def extract_reasoned_reply(response, *, answer_field: str = "answer") -> str | None:
    """Return the answer field from a forced-tool response, or None if the tool call is
    missing/empty/malformed (caller falls back). The thinking field is never returned."""
```

- `answer_formatter` already injects `AI_Agents/src` onto `sys.path` (parents[4]) to import `persona`
  without an import cycle; it imports `from reasoned_reply import ...` the same way. **No app-layer re-export
  needed.**
- The two `market_commentary` modules are already in `AI_Agents/src` and import siblings directly.

### Per-surface migration

| Surface | Seam | Tool / fields | Contract |
|---|---|---|---|
| **answer_formatter** | inside `_invoke_llm` (`formatter.py:151`), which is "isolated so tests can patch it" | `return_formatted_answer(reasoning, answer)`, forced `tool_choice` | `format_answer` still returns a markdown `str` — callers untouched. Keep `cache_control: ephemeral` on the system block (`formatter.py:167`) and the `stop_reason == "max_tokens"` → `FormatterFailure` guard (`formatter.py:172-175`). Bump `max_tokens` 2000 → 2600. |
| **market QA** (`chat_qa.py:21`) | replace `QA_PROMPT \| llm \| StrOutputParser()` with a forced-tool invoke + `extract_reasoned_reply` | `return_qa_answer(reasoning, answer)`, forced `tool_choice` (Sonnet) | `answer_question` still returns `str`. Bump `max_tokens` 1024 → 1500. |
| **doc generation** (`document_generator.py:77`) | replace `... \| llm \| StrOutputParser()` with a forced-tool invoke + extract | `return_commentary_document(outline, document)` — discarded field is **`outline`** (a 2-page artifact benefits from a planned structure and it absorbs preamble) | `generate_document` still returns `str`. Bump `max_tokens` 3072 → 3600. Lowest-ROI of the three (once-daily job, not per-user chat) — included for completeness; outline-first keeps it consistent and near-zero-cost. |

`general_chat`'s `_RETURN_REPLY_TOOL` / forced `tool_choice` / extraction-with-fallback
(`general_chat_engine.py:127,326,337-362`) is the working reference implementation to mirror for the extract +
fallback behavior.

## Testing

### Before/after eval (technique #5 doing its job)

- **answer_formatter is already exercised** by the 16-question voice eval (`questions_voice.yaml`) via the
  AA / rebalancing / goal-planning turns (`v_aa_plan`, `v_aa_why`, `v_gp_feasible`, `v_aa_intl`, `v_gp_retire`,
  `v_aa_emergency`, `v_rebal`). Procedure: snapshot the **current** state as the new baseline *before* the
  change (`snapshots/pre-reasoning-2026-06-15.json`), apply changes, re-run, render the two-column diff.
  Pass condition: the `9.1/10` raw-score and "here's what the numbers show" leaks disappear; answers don't
  otherwise regress.
- **QA + doc-gen are NOT reachable through `ChatBrain.run_turn`** (market chat routes to `general_chat`, not
  `market_commentary`). So add two small **targeted before/after snapshots**, run directly (not via the chat
  brain):
  - QA: a fixed `market_commentary_latest.md` fixture + 2-3 fixed questions → `answer_question(...)`.
  - doc-gen: a fixed `MacroSnapshot` fixture → `generate_document(...)`.
  These need a real LLM call, so gate them behind the existing `ENABLE_LLM_SMOKE=1` / `real_llm` marker.

### Leakage flags (cheap automated signal in the diff)

Extend `build_diff_html.py`'s flag detection (currently Tilly / million / billion) with:
- raw risk score: regex `\b\d+(?:\.\d+)?\s*/\s*10\b`.
- preamble / working-out phrases: `here's what`, `looking at your`, `based on the data`, `let me`.
These flag the "after" column so regressions pop without manual reading.

### Unit tests (no network)

- `reasoned_reply.py`: `reasoned_reply_tool` puts `thinking_field` first and marks both required;
  `extract_reasoned_reply` returns the answer for a well-formed tool call and `None` for missing/empty/malformed.
- Per surface (mock the LLM to return a tool call with `reasoning="INTERNAL WORKING OUT"` + a clean answer):
  assert the returned string equals the answer and **never contains the reasoning text** — i.e. the field is
  truly discarded. For `answer_formatter`, patch at the `ChatAnthropic`/`llm.invoke` boundary (deeper than
  `_invoke_llm`) since `_invoke_llm` itself is what changes.

## Rollout order (each step independently verifiable)

0. **Snapshot current state as the new baseline** (`pre-reasoning-2026-06-15.json`) — time-critical, cannot be
   reconstructed once prompts change.
1. Add `reasoned_reply.py` + its unit tests. Pure addition, nothing wired yet.
2. Migrate **market QA** (simplest free-text → forced-tool conversion; proves the helper end-to-end).
3. Migrate **doc generation** (outline-first variant).
4. Migrate **answer_formatter** (highest value + highest care: preserve `str` contract, cache_control, and the
   truncation guard). Re-run the chat eval immediately — it covers this surface.
5. Add leakage flags to `build_diff_html.py` + the two targeted QA/doc-gen snapshots.
6. Re-run the full eval → render two-column before/after → review whether the leaks are gone.

## Risks / open questions

- **Does the reasoning field actually improve answers, or just cost tokens?** That's exactly what the
  before/after eval measures. If the diff shows no improvement on a surface, revert that surface — the change
  is cheap and isolated.
- **Truncation:** `reasoning` + `answer` must fit `max_tokens`. Budgets are bumped per surface; the existing
  `answer_formatter` `stop_reason` guard already converts a truncated reply into the deterministic fallback
  brief, so the worst case degrades safely.
- **Latency/cost:** one call per surface (unchanged round-trips); only the extra discarded tokens. Net latency
  ≈ flat.
- **Determinism:** `answer_formatter` currently runs at default temperature (unset, so the Anthropic API
  default); the before/after diff
  carries the same sampling noise as the persona eval (mitigated by fixed fixtures, not eliminated). We are
  **not** changing temperature here (out of scope).
- **Sonnet + forced `tool_choice`:** supported; QA stays on Sonnet.
