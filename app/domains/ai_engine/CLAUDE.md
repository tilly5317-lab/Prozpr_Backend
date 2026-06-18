# app/domains/ai_engine/ — the chat brain

Owns ONLY the orchestration of a chat turn — no per-intent/per-domain logic (that lives in each owning domain).

## Entry / contract
- Public API: `from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult`.
- `services/` holds `brain.py` (`ChatBrain.run_turn` — classify intent, run the matching flow) and `flow.py` (the `FLOWS` table: intent → ordered domain calls).

## The turn (`ChatBrain.run_turn`)
1. `build_turn_context(turn)` → `TurnContext` (history + last module runs + active intent + `awaiting_save` gate).
2. Run the always-first `intent_classifier` → `IntentDecision`. Classifier-only intents (`out_of_scope`, `stock_advice`) short-circuit with a canned message.
3. `FLOWS[intent.name]` (or `flow_general_chat` for unknown) picks the flow. A legacy `ctx.awaiting_save` override (→ `flow_goal_planning`) still sits in `_flow_for`, but it is **inert**: no handler raises the flag since the AA/rebalancing "save it" follow-up was removed, so the override never fires today.
4. `await flow(turn, ctx)` under a per-flow timeout → final `ModuleOutput`.
5. `_finalize` shapes `ChatBrainResult` + writes telemetry (best-effort).

## Flows (`services/flow.py`)
A flow is the ONLY place domains are composed; each calls one or more domain `run(turn, ctx, prior)` entry points in order:
```
FLOWS = {
  "asset_allocation":     flow_asset_allocation,    # [asset_allocation]
  "portfolio_query":      flow_portfolio_query,     # [portfolio] (read-only)
  "general_chat":         flow_general_chat,         # [general_chat]
  "rebalancing":          flow_rebalancing,          # [asset_allocation, rebalancing]
  "goal_planning":        flow_goal_planning,        # [cashflow]
  "general_market_query": flow_market,               # [market_commentary, general_chat]
}
```
Adding/altering an intent = one new `flow_*` + one `FLOWS` row. The brain never changes.

## Shared chat kernel (package root, not `services/`)
Contracts/utilities used across domains — not domain logic:
```
types.py          ModuleOutput / IntentDecision / AIModule (the contract)
chat_types.py     ChatTurnInput / ChatBrainResult (brain I/O DTOs)
turn_context.py   TurnContext + build_turn_context
common.py         sys.path inject (ensure_ai_agents_path), tracing, money fmt
classifier_llm.py shared Haiku structured-output helper
chat_dispatcher.py per-intent chat-handler registry + dispatch
answer_formatter/ shared question-aware answer formatter
visualizations/   chart-payload builders + registry
```

## Where the per-intent work lives (owning domains)
| intent / module   | domain entry (`run`)                                              |
|-------------------|------------------------------------------------------------------|
| intent_classifier | `intent_classifier/services/intent_classifier_service.py`        |
| asset_allocation  | `asset_allocation/services/asset_allocation_module_service.py`   |
| rebalancing       | `rebalancing/services/rebalancing_module_service.py`             |
| cashflow          | `cashflow/services/cashflow_module_service.py`                   |
| portfolio_query   | `portfolio/services/portfolio_query_service.py`                  |
| market_commentary | `market_commentary/services/market_commentary_module_service.py`|
| general_chat      | `general_chat/services/general_chat_module_service.py`           |

Each domain keeps its agent implementation in its own `services/<engine>/` subpackage (e.g. `rebalancing/services/rebal_engine/`).

## Gotchas & invariants
- **A domain never calls another domain.** Cross-domain data is produced by one domain and passed to the next via the `prior` dict (`services/flow.py`).
- **Delegate AI to `AI_Agents/src`, never hand-roll Claude.** A domain service does CRUD + calls the ready-made agent (via `ensure_ai_agents_path()` from `ai_engine.common`); never write `ChatAnthropic` / `messages.create` for a reply an agent already produces.
- **`routers/` here are debug / direct-invocation endpoints, NOT the live chat path** (chat goes through `ChatBrain`) — don't wire production behavior into them (`routers/__init__.py`).
- Flows run under a per-flow timeout; on timeout the brain returns a fallback `ModuleOutput` rather than erroring (`services/brain.py`).

## Don't read
- `__pycache__/`, `tests/`.
