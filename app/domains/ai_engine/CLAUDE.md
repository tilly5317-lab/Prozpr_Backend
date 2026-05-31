# app/domains/ai_engine/ — the chat brain

This domain owns ONLY the orchestration of a chat turn. It contains NO
per-intent / per-domain logic — that lives in each owning domain.

`services/` holds **exactly two files**:

```
services/
  brain.py   # ChatBrain.run_turn — classify intent, run the matching flow
  flow.py    # FLOWS: intent name -> ordered sequence of domain functions
```

Public API:

```python
from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult
```

## The turn (`ChatBrain.run_turn`)

1. `build_turn_context(turn)` → `TurnContext` (history + last module runs +
   active intent + `awaiting_save` gate).
2. Run the always-first `intent_classifier` (from the intent_classifier
   domain) → `IntentDecision`.
3. Classifier-only intents (`out_of_scope`, `stock_advice`) short-circuit with
   their canned message.
4. `FLOWS[intent.name]` (or `flow_general_chat` for unknown) picks the flow;
   `ctx.awaiting_save` overrides to the cashflow save flow.
5. `await flow(turn, ctx)` under a per-flow timeout — the flow composes the
   domain calls and returns the final `ModuleOutput`.
6. `_finalize` shapes `ChatBrainResult` + writes telemetry (best-effort).

## Flows (`services/flow.py`)

A flow is the ONLY place domains are composed. Each calls one or more domain
`run(turn, ctx, prior)` entry points in order:

```python
FLOWS = {
  "asset_allocation":     flow_asset_allocation,   # [asset_allocation]
  "portfolio_query":      flow_portfolio_query,    # [portfolio] (read-only)
  "general_chat":         flow_general_chat,        # [general_chat]
  "rebalancing":          flow_rebalancing,         # [asset_allocation, rebalancing]
  "goal_planning":        flow_goal_planning,       # [cashflow]
  "general_market_query": flow_market,              # [market_commentary, general_chat]
}
```

Rule: a flow may call several domains, but **a domain never calls another
domain**. Cross-domain data (e.g. the allocation target rebalancing needs) is
produced by one domain and passed to the next via the `prior` dict.

Adding/altering an intent = one new `flow_*` + one row in `FLOWS`. The brain
never changes.

## Shared chat kernel (package root, not `services/`)

These are contracts/utilities used across domains — not domain logic:

```
types.py          ModuleOutput / IntentDecision / AIModule (the contract)
chat_types.py     ChatTurnInput / ChatBrainResult (brain I/O DTOs)
turn_context.py   TurnContext + build_turn_context
common.py         sys.path inject (ensure_ai_agents_path), tracing, money fmt
classifier_llm.py shared Haiku structured-output helper (classify_action)
chat_dispatcher.py per-intent chat-handler @register registry + dispatch_chat
answer_formatter/ shared question-aware answer formatter
visualizations/   chart-payload builders + registry
```

## Where the per-intent work lives (owning domains)

| intent / module     | domain entry (`run`)                                                   |
|---------------------|-----------------------------------------------------------------------|
| intent_classifier   | `intent_classifier/services/intent_classifier_service.py`             |
| asset_allocation    | `asset_allocation/services/asset_allocation_module_service.py`        |
| rebalancing         | `rebalancing/services/rebalancing_module_service.py`                  |
| cashflow            | `cashflow/services/cashflow_module_service.py`                        |
| portfolio_query     | `portfolio/services/portfolio_query_service.py` (`answer_portfolio_query`) |
| market_commentary   | `market_commentary/services/market_commentary_module_service.py`     |
| general_chat        | `general_chat/services/general_chat_module_service.py`                |

Each domain keeps its agent implementation in its own subpackage
(`<domain>/services/<engine>/`), e.g. `asset_allocation/services/aa_engine/`,
`rebalancing/services/rebal_engine/`, `cashflow/services/goal_planning_engine/`.

## How a domain service produces its reply (the scaling rule)

A domain's `services/` does two kinds of work and **nothing else**:

1. **CRUD / persistence** — normal DB reads + writes for that domain (its
   `*_persist_service.py`, repositories, model reads).
2. **AI work — delegated to `AI_Agents/src`, never to Claude directly.** When a
   reply needs an agent, the service calls `ensure_ai_agents_path()` (from
   `app.domains.ai_engine.common`) and imports the ready-made agent from
   `AI_Agents/src/<module>` (e.g. `from asset_allocation_pydantic.pipeline import
   run_allocation_with_state`, `from Rebalancing.pipeline import run_rebalancing`,
   `from cashflow_statement.engine import compute_full_projection`,
   `from portfolio_query import PortfolioQueryOrchestrator`). The agent already
   does the prompting/formatting — the service builds the input, calls it,
   persists, and returns the text. **Do NOT hand-roll `ChatAnthropic` /
   `messages.create` for a reply an agent already produces.**

This is what makes the system scalable. Adding a brand-new AI capability is
exactly four steps and touches no existing domain:

1. Create the new domain folder `app/domains/<new>/` (models/schemas/routers/
   services) with its CRUD.
2. In `<new>/services/<new>_module_service.py`, add
   `run(turn, ctx, prior) -> ModuleOutput` that builds input, calls the agent
   from `AI_Agents/src/<new_agent>`, persists, and returns the reply.
3. Emit the new intent name from `intent_classifier`.
4. Add one `flow_<new>` + one row in `FLOWS` (`services/flow.py`).

The brain, and every other domain, stay untouched.

## Tests

`app/domains/ai_engine/tests/` — brain + turn_context + classifier-helper
tests. Engine tests are co-located in each domain's engine subpackage.

## Don't read

- `__pycache__/`, `tests/`.

## Refresh

If this looks stale after a structural change, run `/refresh-context` here.
