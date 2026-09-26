# app/domains/ai_engine/ — the chat brain

Owns ONLY the orchestration of a chat turn — no per-intent/per-domain logic (that lives in each owning domain).

## Entry / contract
- Public API: `from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult`.
- `ChatBrain.run_turn` is the whole surface: context → classify → gate → flow → result. The chat router, never the brain, commits.

## Layers
- **services/** — `brain.py` (`run_turn`, sequence below) + `flow.py` (the `FLOWS` table: intent → ordered domain calls).
- **answer_formatter/** — THE answer stage; every module's reply is written here.
- **schemas/** — per-module chat payloads (conversation, intent, market, portfolio, rebalancing, status).
- **routers/** — DEBUG endpoints only, never the live chat path (see Gotchas).
- **visualizations/** — chart specs a reply can carry (`category_gap_bar`).
- **package root** — the shared chat kernel, deliberately NOT under `services/`; listed below.

### The turn (`ChatBrain.run_turn`)
1. `build_turn_context(turn)` → `TurnContext` (history + last module runs + active intent).
2. Run the always-first `intent_classifier` → `IntentDecision`. Classifier-only intents (`out_of_scope`, `stock_advice`) short-circuit with a canned message. The classifier's `tools_needed` is copied onto `ctx.tools_needed` here (`services/brain.py:243`; see gotcha).
3. **Portfolio gate** — a portfolio-dependent intent with no imported MF holdings short-circuits here, before any flow runs: an honest ask for the CAS statement plus the add-CAMS CTA flag (`portfolio_gate.py`; see gotcha).
4. `FLOWS[intent.name]` (or `flow_general_chat` for unknown) picks the flow — a plain lookup (`_flow_for`). The legacy `awaiting_save` override is gone (nothing writes the field).
5. `await flow(turn, ctx)` under a per-flow timeout → final `ModuleOutput`; on timeout the brain returns a fallback `ModuleOutput` rather than erroring.
6. `_finalize` shapes `ChatBrainResult` + writes telemetry (best-effort).

### Flows (`services/flow.py`)
A flow is the ONLY place domains are composed; each calls domain `run(turn, ctx, prior)` entry points in order:
```
asset_allocation · portfolio_query (read-only) · general_chat · goal_planning
mutual_fund_query (read-only) · additional_investment
rebalancing          → [asset_allocation, rebalancing]
general_market_query → [market_commentary, general_chat]
```
Single-domain flows call the same-named domain. Adding an intent = one `flow_*` + one `FLOWS` row; the brain never changes.

### The shared chat kernel (package root)
Cross-domain contracts and utilities, never domain logic. `types.py` carries the `ModuleOutput`/`IntentDecision`/`AIModule` contract — run-ids and the `show_preferences_pill` / `has_candidate_preference` flags a module raises for the client — alongside `chat_types.py`, `turn_context.py`, `classifier_llm.py` (Haiku structured-output helper) and `chat_dispatcher.py` (per-intent handler registry). Load-bearing edges:
- `common.py` — `ensure_ai_agents_path()` sys.path inject, tracing, money fmt.
- `streaming.py` — re-exports `AI_Agents/src/token_stream.py`; the canonical def lives under `src/` because agents cannot import `app/`. The PI persona is likewise imported from `AI_Agents/src` directly.
- `thinking.py` — live "thinking aloud" feed, polled via `GET /chat/sessions/{id}/thinking`.
- `logic_docs.py` — module→Logics-thesis-doc loader; formatter attaches docs on educate/narrate.
- `usage_tracking.py`/`posthog_tracing.py` — per-turn token accounting + zero-touch PostHog LLM tracing.

## Gotchas & invariants
- **Every customer-facing reply is written by `answer_formatter`.** A domain produces a facts pack + a body prompt; the formatter owns PI's voice, the house rules, streaming and the failure path. When domains each owned a reply call, the rules got copy-pasted into three prompts. Never hand-roll one.
- **The formatter's tool may gain non-prose fields, never a second prose field.** `extra_tool_fields`/`extras_out` carry booleans, enums and short control strings. A second PROSE field competes with `answer` — that broke the old reasoning-first scratchpad, which returned no answer about half the time on long replies (`answer_formatter/formatter.py`).
- **Chat never WRITES a preference, and no longer runs preference what-ifs.** Any preference-shaped ask across rebalancing / asset_allocation / additional_investment lands on one shared `PREFERENCE_REDIRECT_MESSAGE` (`profile/services/preference_view.py:174`) plus the pill. It rides the facts pack as `preference_pointer` so the formatter writes the whole reply — concatenating a pointer onto a finished answer is what let the copy drift across three modules. SAVED preferences still shape every plan and are still disclosed; only the change path is retired, behind an unreferenced re-enable seam.
- **A servable override survives a preference redirect.** A turn carrying both (a cash injection plus "more equity") still runs the override — discarding the whole turn on the preference half was the earlier bug in all three dispatchers.
- **The portfolio gate FAILS OPEN.** If the holdings check raises it logs and returns `False`, so the flow runs anyway (`portfolio_gate.py:101`) — a broken check must never lock a customer out of chat.
- **A domain never calls another domain.** Cross-domain data is produced by one domain and passed to the next via the `prior` dict (`services/flow.py`).
- **Delegate AI to `AI_Agents/src`, never hand-roll Claude.** A domain service does CRUD + calls the ready-made agent via `ensure_ai_agents_path()`.
- **`routers/` here are debug endpoints, NOT the live chat path** — don't wire production behaviour into them (`routers/__init__.py`).
- **`ctx.tools_needed` is a fetch list, not routing.** Empty means the customer's own record suffices; its members select market context for `flow_market` and `portfolio_query`. Loading commentary unconditionally made the model compare an allocation % against a P/E.
- **`action_mode` is NOT set by the classifier** — `ClassificationResult` has no mode field; each module's detector picks it after routing. The union is `ActionMode` in `answer_formatter/formatter.py`; a re-run is `compute` + `is_rerun: true`, not a `recompute` mode.
- **The formatter is the one `temperature` indirection** allowed by the root convention: kwargs dict + `AILAX_FORMATTER_TEMPERATURE`, default `"0"`.
- **Streamed deltas are provisional; `run_turn`'s return wins.** Nothing streams unless a `TokenStream` is open, so the blocking path is untouched. The sink is in process memory — the streaming request and the turn must share one uvicorn worker.

## Don't read
- `__pycache__/`, `tests/`.
