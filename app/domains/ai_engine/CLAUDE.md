# app/domains/ai_engine/ — the chat brain

Owns ONLY the orchestration of a chat turn — no per-intent/per-domain logic (that lives in each owning domain).

## Entry / contract
- Public API: `from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult`.
- `services/` holds `brain.py` (`ChatBrain.run_turn` — classify intent, run the matching flow) and `flow.py` (the `FLOWS` table: intent → ordered domain calls).

## The turn (`ChatBrain.run_turn`)
1. `build_turn_context(turn)` → `TurnContext` (history + last module runs + active intent).
2. Run the always-first `intent_classifier` → `IntentDecision`. Classifier-only intents (`out_of_scope`, `stock_advice`) short-circuit with a canned message. The classifier's `tools_needed` is copied onto `ctx.tools_needed` here (`services/brain.py:232`; see gotcha).
3. `FLOWS[intent.name]` (or `flow_general_chat` for unknown) picks the flow — a plain lookup (`_flow_for`). The legacy `awaiting_save` override is gone (nothing writes the field).
4. `await flow(turn, ctx)` under a per-flow timeout → final `ModuleOutput`; on timeout the brain returns a fallback `ModuleOutput` rather than erroring.
5. `_finalize` shapes `ChatBrainResult` + writes telemetry (best-effort).

## Flows (`services/flow.py`)
A flow is the ONLY place domains are composed; each calls domain `run(turn, ctx, prior)` entry points in order:
```
FLOWS = {
  "asset_allocation":      flow_asset_allocation,      # [asset_allocation]
  "portfolio_query":       flow_portfolio_query,       # [portfolio] (read-only)
  "general_chat":          flow_general_chat,          # [general_chat]
  "rebalancing":           flow_rebalancing,           # [practical_asset_allocation, rebalancing]
  "financial_planning":    flow_financial_planning,    # [financial_planning, (cashflow)]
  "general_market_query":  flow_market,                # [market_commentary, general_chat]
  "additional_investment": flow_additional_investment, # [additional_investment]
  "mutual_fund_query":     flow_mutual_fund_query,     # [mutual_funds] (read-only)
}
```
Adding/altering an intent = one new `flow_*` + one `FLOWS` row; the brain never changes. Each intent's `run` lives in its owning domain's `services/` (agent in a `services/<engine>/` subpackage); see that domain's CLAUDE.md.

## Shared chat kernel (package root, not `services/`)
Cross-domain contracts/utilities, not domain logic: `types.py` (the `ModuleOutput`/`IntentDecision`/`AIModule` contract), `chat_types.py`, `turn_context.py`, `classifier_llm.py` (Haiku structured-output helper), `chat_dispatcher.py` (per-intent handler registry), `visualizations/`, `schemas/`. Load-bearing edges:
- `planning_gate.py` / `portfolio_gate.py` — the two pre-flow guards. Both sit between classification and flow selection and both FAIL OPEN. `planning_gate` replaced the old `profile_gate` + `goal_gate` pair when those intents merged.
- `answer_formatter/` — THE answer stage; every module's reply is written here.
- `common.py` — `ensure_ai_agents_path()` sys.path inject, tracing, money fmt.
- `streaming.py`/`persona.py` re-export `AI_Agents/src/token_stream.py`/`persona.py`; the canonical defs live under `src/` because agents (e.g. portfolio_query) cannot import `app/`.
- `thinking.py` — live "thinking aloud" feed, polled via `GET /chat/sessions/{id}/thinking`.
- `logic_docs.py` — module→Logics-thesis-doc loader; formatter attaches docs on educate/narrate.
- `usage_tracking.py`/`posthog_tracing.py` — per-turn token accounting + zero-touch PostHog LLM tracing.
- Per-module telemetry is `chat.services.ai_module_telemetry.record_ai_module_run` (`chat_ai_module_runs`, logger `ailax.ai_bridge`). `financial_planning` writes the richest trail there — grep `AILAX_FP_READ` / `AILAX_FP_STAGED` / `AILAX_FP_WRITE` / `AILAX_FP_EFFECTS` / `AILAX_FP_UNDO` for a whole turn.

## Gotchas & invariants
- **Every customer-facing reply is written by `answer_formatter`.** A domain produces FACTS (a facts pack) + a body prompt; the formatter owns PI's voice, the house rules, streaming and the failure path. Domains once each owned their reply LLM call — the rules got copy-pasted into three prompts and token-streaming had to land in four places. Never hand-roll a reply call; add a facts pack.
- **The formatter's tool may gain non-prose fields, never a second prose field.** `extra_tool_fields`/`extras_out` carry booleans, enums and short control strings (guardrail verdict, `path`, `suggested_intent`). A second PROSE field competes with `answer` — that broke the old reasoning-first scratchpad, which returned no answer about half the time on long replies (`answer_formatter/formatter.py` `_invoke_llm`).
- **A domain never calls another domain.** Cross-domain data is produced by one domain and passed to the next via the `prior` dict (`services/flow.py`).
- **Delegate AI to `AI_Agents/src`, never hand-roll Claude.** A domain service does CRUD + calls the ready-made agent (via `ensure_ai_agents_path()`); never write `ChatAnthropic`/`messages.create` for a reply an agent already produces.
- **`routers/` here are debug endpoints, NOT the live chat path** (chat goes through `ChatBrain`) — don't wire production behavior into them (`routers/__init__.py`).
- **The planning gate can override the ROUTE, never the recorded intent.** An open question or a half-built goal claims the turn whatever the classifier said, because mid-thread fragments ("50 lakhs down", "no, everything's the same") genuinely are ambiguous out of context — the context lives in the row. The telemetry row still records what the customer was understood to have asked (`services/brain.py`, `_flow_for`). `out_of_scope` deliberately cannot interrupt an open thread: that is exactly the label an unanchored fragment gets. The module hands the turn back (`side_effects["handoff"]`) once it has READ the message and found nothing about the plan in it.
- **`ctx.tools_needed` is a fetch list, not routing.** Empty means the customer's own record suffices. Its one member gates the market commentary in `portfolio_query`; loading that unconditionally made the model compare an allocation % against a P/E.
- **`action_mode` is NOT set by the classifier** — `ClassificationResult` has no mode field; each module's detector picks it after routing. Union: `ActionMode` in `answer_formatter/formatter.py` (`recompute`/`recompute_full` are gone — a re-run is `compute` + `is_rerun: true`).
- **Every `ChatAnthropic(...)` pins `temperature` explicitly** (root convention). The formatter is the one exception: kwargs dict + `AILAX_FORMATTER_TEMPERATURE`, default `"0"`.
- **Streamed deltas are provisional; `run_turn`'s return wins.** Nothing streams unless a `TokenStream` is open, so the blocking path is untouched. The sink is in process memory — the streaming request and the turn must share one uvicorn worker (like `app.core.progress`).

## Don't read
- `__pycache__/`, `tests/`.
