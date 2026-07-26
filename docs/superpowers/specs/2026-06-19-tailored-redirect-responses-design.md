# Tailored redirect responses for `out_of_scope` & `stock_advice`

**Date:** 2026-06-19
**Status:** Approved (pending spec review)
**Topic:** Route the two classifier-only intents through the shared answer formatter so the reply acknowledges the customer's actual question before redirecting, instead of returning a fixed canned string.

---

## 1. Problem

Two intents are "classifier-only" and never run a flow:

- `out_of_scope`
- `stock_advice` (referred to verbally as "talk advice" — a voice-to-text rendering of "stock advice")

Today, when the classifier picks either of these, `ChatBrain.run_turn()` short-circuits and returns a **fixed canned string** verbatim (`brain.py:130–142`). The reply ignores what the customer actually asked. Every other module instead builds a **facts pack** and passes it — together with the customer's question — through the shared **answer formatter**, which restates the question and answers it in PI's voice.

We want these two intents to follow that same structure: the canned message becomes the facts-pack payload, and `(customer question + canned message)` flows through the formatter to produce a tailored acknowledge-then-redirect reply.

A second, latent issue surfaced during tracing: `out_of_scope` has six sub-reasons, each with a more specific canned line in `_OOS_REPLIES_BY_SUBREASON` (`general_chat_engine.py:36–67`), but **those lines are dead on the short-circuit path** — today every `out_of_scope` reply is the single generic message regardless of sub-reason.

---

## 2. Current state (verified)

- **`ctx` (TurnContext) already exists at the short-circuit.** It is built at `brain.py:114` (`build_turn_context`), before the classifier runs (line 121) and before the short-circuit (lines 130–142). It carries `user_question`, `conversation_history`, `user_ctx`, `db`, `session_id`, `effective_user_id`. Nothing needs to move.
- **The canned string is already resolved** as `canned = intent.raw.out_of_scope_message`: `STOCK_ADVICE_MESSAGE` for `stock_advice`, `OUT_OF_SCOPE_MESSAGE` for `out_of_scope` (set in `AI_Agents/src/intent_classifier/classifier.py:196–215`). This string is the authoritative statement of what PI does / doesn't do.
- **Sub-reason** is available as `intent.raw.out_of_scope_subreason` (enum `OutOfScopeSubreason`, values: `gibberish`, `identity_or_meta`, `security_or_credentials`, `chat_summary`, `off_topic`, `other`). For `out_of_scope` it is always set (defaults to `OTHER`); for `stock_advice` it is `None`.
- **The sub-reason-specific lines exist but are unreachable** on this path (`_OOS_REPLIES_BY_SUBREASON`, used only by `generate_general_chat_response`, which the short-circuit never calls).
- **`_finalize(*, text, intent, flow, t0, db, uid, sid, final=None)`** writes `text` straight into `ChatBrainResult.content`. It is the single entry point for the reply text. (`t0=t_all`, the start time from `brain.py:105`.)
- **`intent.raw` IS the `ClassificationResult`** (`intent_classifier_service.py:44`: `raw=classification`). It carries `out_of_scope_message`, `out_of_scope_subreason` (enum or `None`), and `intent`. So the existing resolver can be called directly on it.
- **`_oos_reply(classification)` already exists** (`general_chat_engine.py:70–84`) and resolves the correct canned string for any `(intent, subreason)` — stock_advice (subreason `None`) → `out_of_scope_message` = `STOCK_ADVICE_MESSAGE`; `out_of_scope` `OTHER` → `OUT_OF_SCOPE_MESSAGE`; any other subreason → its `_OOS_REPLIES_BY_SUBREASON` line. It is dead on the short-circuit path today (only reached via `generate_general_chat_response`).
- **Telemetry is safe for new module names.** `record_ai_module_run(module: str, …)` writes a free `String(64)` column (`chat_ai_module_run.py:39`), inside a `try/except SQLAlchemyError` + `begin_nested()` savepoint (`ai_module_telemetry.py:60–89`) — a failed write returns `None` and never raises. So `module="out_of_scope"`/`"stock_advice"` cannot break the reply.

### The shared formatter contract

```python
async def format_with_telemetry(
    *,
    ctx: TurnContext,
    facts_pack: dict[str, Any],
    body_prompt: str,
    module_name: str,
    action_mode: str,
    profile: dict[str, Any],
    build_fallback: Callable[[], str],
) -> str
```

It assembles a system prompt (shared house style + "question-aware" rules) plus the per-module `body_prompt`, then a user message containing `FACTS_PACK`, `PROFILE`, `RECENT_HISTORY`, and `CUSTOMER_QUESTION` (`ctx.user_question`); calls Haiku 4.5; records telemetry; and **on any failure calls `build_fallback()`**. The house style already includes *"restate what the customer asked, then say 'no' plainly when FACTS_PACK can't answer the question"* — exactly the behaviour we want here.

---

## 3. Design

### 3.1 Behaviour matrix

| Intent | Sub-reason | Behaviour |
|---|---|---|
| `stock_advice` | — | **Tailor** via formatter |
| `out_of_scope` | `off_topic` | **Tailor** via formatter |
| `out_of_scope` | `other` | **Tailor** via formatter |
| `out_of_scope` | `gibberish` | **Canned**, verbatim |
| `out_of_scope` | `identity_or_meta` | **Canned**, verbatim |
| `out_of_scope` | `security_or_credentials` | **Canned**, verbatim |
| `out_of_scope` | `chat_summary` | **Canned**, verbatim |

The canned (non-tailored) rows are **activated to use their sub-reason-specific lines** (via the existing `_OOS_REPLIES_BY_SUBREASON`), replacing today's behaviour where they silently fall back to the single generic message. These remain 100% deterministic — no LLM call.

### 3.2 Resolving the canned line — reuse `_oos_reply` in place

The codebase **already has** the resolver: `_oos_reply(classification)` in `general_chat_engine.py:70–84` returns the correct canned string for any `(intent, subreason)` — verified to cover stock_advice (subreason `None` → `STOCK_ADVICE_MESSAGE`), `out_of_scope` `OTHER` → `OUT_OF_SCOPE_MESSAGE`, and every other subreason → its `_OOS_REPLIES_BY_SUBREASON` line. **Do not write a parallel resolver, and do not rename it** — the new handler lives in the same file, so it calls `_oos_reply` directly.

This single resolved line is used **both** as the canned reply (non-tailored rows) **and** as the facts-pack `boundary_message` + `build_fallback` (tailored rows). One source of truth, no divergence. (No new canned imports — `STOCK_ADVICE_MESSAGE` arrives via `intent.raw.out_of_scope_message` through the resolver.)

### 3.3 New code — added to `general_chat_engine.py`

Beside the existing `_oos_reply` / `_OOS_REPLIES_BY_SUBREASON` (no new file):

```python
def should_tailor(intent_name: str, subreason: OutOfScopeSubreason | None) -> bool:
    if intent_name == "stock_advice":
        return True
    if intent_name == "out_of_scope":
        return subreason in {OutOfScopeSubreason.OFF_TOPIC, OutOfScopeSubreason.OTHER}
    return False

async def format_redirect_or_canned(*, ctx, intent) -> str:
    resolved = _oos_reply(intent.raw)                   # ClassificationResult → canned line
    if not should_tailor(intent.name, intent.raw.out_of_scope_subreason):
        return resolved                                 # deterministic, no LLM
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack={"boundary_message": resolved},      # the only fact: PI's scope statement
        body_prompt=_REDIRECT_FORMATTER_BODY,
        module_name=intent.name,                        # "out_of_scope" | "stock_advice" — telemetry
        action_mode="redirect",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: resolved,                # failure == today's canned behaviour
    )
```

Only **one new import** in this file — `format_with_telemetry` from `app.domains.ai_engine.answer_formatter`. Everything else is already present there (`_oos_reply`, `_OOS_REPLIES_BY_SUBREASON`, `OutOfScopeSubreason`, the canned strings). It calls the shared formatter (the same path asset_allocation / rebalancing use), never `ChatAnthropic` directly. The facts pack carries a **single key**: `intent` would duplicate the formatter's `MODULE` field and `subreason` is unused by the body_prompt, so neither is included.

### 3.4 `_REDIRECT_FORMATTER_BODY` (the body_prompt)

Module-specific instructions appended to the shared house style. Key rules:

- This is a **decline / redirect**, not an answer. The customer asked for something PI does not handle.
- Open by briefly acknowledging, in your own words, what the customer actually asked (the question-aware opening already does this).
- State plainly that it's outside what PI helps with today; then redirect to what PI *does* do, using `boundary_message` as the source of truth for PI's scope.
- **Hard guardrail — never provide the out-of-scope content itself:** no individual stock picks or buy/sell calls; no tax, insurance, legal, or medical advice; no password / login / credential help; no answering general-knowledge or off-topic questions. Acknowledge and redirect only.
- For `stock_advice`, convey the existing rationale from `boundary_message`: we don't advise on individual stocks; we focus on diversified funds for long-term goals.
- Keep it short (3–5 sentences), warm, in PI's voice. Cite only `boundary_message`; invent no PI capabilities.

`action_mode` is a required param (no default), interpolated into the prompt (`ACTION_MODE: redirect`) and telemetry (`reason="formatter:redirect"`); `assemble_prompt`/`format_answer`/`format_with_telemetry` all type it `str`, so `"redirect"` runs as a bare string with no branching. **The only edit outside `general_chat` + `brain`:** add `"redirect"` to the `ActionMode` Literal (`formatter.py:38–45`) and update the line-37 comment (currently *"clarify / redirect bypass it"*), which the new usage would otherwise contradict. This is **documentation-only and optional** — the feature works without touching `formatter.py`; the comment just goes stale.

### 3.5 Brain wiring

In `brain.py`, the classifier-only branch (currently `brain.py:130–142`) changes its reply-text computation only:

```python
if intent.name in _CLASSIFIER_ONLY_INTENTS and intent.raw is not None:
    canned = getattr(intent.raw, "out_of_scope_message", None)
    if canned:
        text = await format_redirect_or_canned(ctx=ctx, intent=intent)
        return await self._finalize(
            text=text, intent=intent, flow=flow, t0=t_all, db=db, uid=uid, sid=sid,
        )
```

The `if canned:` guard and the fall-through to flow dispatch when `canned` is falsy are preserved unchanged.

**Import / architecture note.** `brain.py` imports `format_redirect_or_canned` from `general_chat_engine`. No cycle: `general_chat_engine` imports `ai_engine.answer_formatter` (and the `intent_classifier` models it already uses) — none import `brain`; `general_chat` is already in the brain's import graph via `flow.py`. This is a deliberate, narrow deviation from the "domains composed only via flows" convention: the classifier-only short-circuit intentionally skips flow dispatch (for efficiency), so it calls one general_chat helper directly rather than running a flow.

---

## 4. Data flow

```
classifier → intent (out_of_scope | stock_advice); intent.raw = ClassificationResult
brain      → format_redirect_or_canned(ctx, intent)
              ├─ _oos_reply(intent.raw)                   → resolved canned line
              ├─ should_tailor == False  → return resolved (deterministic, no LLM)
              └─ should_tailor == True   → format_with_telemetry(
                                              facts_pack={boundary_message: resolved, ...},
                                              CUSTOMER_QUESTION = ctx.user_question,
                                              build_fallback = () -> resolved)
brain      → _finalize(text=...) → ChatBrainResult.content → customer
```

---

## 5. Error handling & no-regression guarantee

- **Formatter failure** (LLM error, empty/truncated output, malformed tool call) → `build_fallback()` returns `resolved`, the exact canned string. Worst case equals today's behaviour (or slightly better, since `resolved` is now sub-reason-specific).
- **Non-tailored rows** never call the LLM — zero new failure surface.
- **`canned` falsy** → unchanged fall-through to flow dispatch.
- **Latency:** tailored rows add one Haiku call (~hundreds of ms) on `off_topic` / `other` / `stock_advice`. Acceptable for these low-frequency intents; non-tailored rows are unaffected.

---

## 6. Testing & success criteria

**Deterministic — must pass (unit tests):**
- **Routing:** `should_tailor(intent, subreason)` returns the exact matrix decision for all 7 rows, incl. `stock_advice` (subreason `None`) and `out_of_scope` defaulting to `OTHER`.
- **Canned activation:** for `gibberish` / `identity_or_meta` / `security_or_credentials` / `chat_summary`, `format_redirect_or_canned` returns the sub-reason-specific line **verbatim** and makes **no LLM call** (regression-lock the exact strings; assert the formatter isn't invoked).
- **Fallback:** with the formatter forced to raise `FormatterFailure`, the returned text equals `_oos_reply(intent.raw)` exactly — tailored rows never regress below today's canned reply.

**Judged — quality, eval harness (non-blocking gate):** adversarial out-of-scope prompts must acknowledge + redirect and must NOT answer the topic — "Which stock should I buy?" (no ticker/call), "How do I file my taxes?" (no instructions), "What's the weather?" / "Tell me a joke" (no answer). These assert LLM behaviour, so they live in the eval harness / manual review, not the deterministic suite.

---

## 7. Files touched

| File | Change |
|---|---|
| `app/domains/general_chat/services/general_chat_engine.py` | Add `should_tailor`, `format_redirect_or_canned`, `_REDIRECT_FORMATTER_BODY`; add one import (`format_with_telemetry`). Reuse the existing `_oos_reply` as-is (no rename). |
| `app/domains/ai_engine/services/brain.py` | Classifier-only branch: compute `text` via `format_redirect_or_canned(ctx, intent)` (~1 line changed) + one import. |
| `app/domains/ai_engine/answer_formatter/formatter.py` | *(Optional, doc-only.)* Add `"redirect"` to the `ActionMode` Literal + update the line-37 comment. No behaviour change. |
| Tests (general_chat test dir) | Routing, canned-activation, and fallback unit tests; adversarial redirect cases in the eval harness. |

No new files. No changes to the classifier, the intent schema, the formatter's runtime behaviour, or `_finalize`. `_oos_reply`, `_OOS_REPLIES_BY_SUBREASON`, and the canned strings are reused — not duplicated or renamed.

---

## 8. Out of scope (YAGNI)

- No changes to how intents are classified, or to the sub-reason taxonomy.
- No new canned message strings.
- No base class / generalized "redirect module" abstraction — a single small handler is enough for two intents.
- No change to the four sensitive sub-reasons' decision to stay deterministic (only their text is upgraded to the sub-reason-specific line).

---

## 9. Audit verification (2026-06-19)

Every load-bearing claim was checked against source before approval.

**Confirmed:**
- `ctx` is built at `brain.py:114`, before the short-circuit (`brain.py:130–141`); carries `user_question`, `conversation_history`, `user_ctx`, `db`, `session_id`, `effective_user_id`.
- `_CLASSIFIER_ONLY_INTENTS = {"out_of_scope", "stock_advice"}` (`brain.py:68–73`).
- `intent.raw` is the `ClassificationResult` (`intent_classifier_service.py:40–45`).
- `_oos_reply` resolves correctly for all `(intent, subreason)`; `_OOS_REPLIES_BY_SUBREASON[OTHER] == OUT_OF_SCOPE_MESSAGE` (`general_chat_engine.py:66, 70–84`).
- `format_with_telemetry` passes `ctx.user_question` as the question and calls `build_fallback()` on `FormatterFailure` (`formatter.py:243–305`); params typed `str`.
- Telemetry `module` is a free `String(64)`, written best-effort behind a savepoint (`chat_ai_module_run.py:39`, `ai_module_telemetry.py:60–89`) — new module names are safe.
- No import cycle introduced.

**Corrections folded in (audit pass 1 — vs. first draft):**
1. Reuse the existing `_oos_reply` resolver instead of a new `resolve_canned`; drop the unneeded `STOCK_ADVICE_MESSAGE` import.
2. `action_mode="redirect"` needs the `ActionMode` Literal/comment kept consistent (documentation-only).
3. `format_redirect_or_canned` signature simplified to `(ctx, intent)`.

**Simplifications folded in (audit pass 2 — Karpathy / "no unnecessary changes"):**
4. **No new file** — add the ~15 lines to `general_chat_engine.py` beside the resolver/canned content (fewest files, highest cohesion).
5. **No rename** — call `_oos_reply` directly (same file now); `general_chat_engine.py` is otherwise unchanged.
6. **Drop `build_redirect_facts_pack`** and the speculative `intent` / `subreason` facts-pack keys (`intent` duplicates `MODULE`; `subreason` is unused) → facts pack is just `{"boundary_message": resolved}`.
7. The `ActionMode` Literal edit is marked **optional / doc-only** — the feature works without touching `formatter.py`.
8. Test criteria split into deterministic must-pass vs. eval-harness judged.
