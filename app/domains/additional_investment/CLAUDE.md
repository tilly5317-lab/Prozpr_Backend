# app/domains/additional_investment/ — deploy fresh money (lumpsum/SIP) into specific funds to BUY

## Entry / contract
- `additional_investment_module_service` is the ONLY gateway to the additional-investment AI module — the brain calls its `run(turn, ctx, prior)`. The pure engine that picks the funds lives in `AI_Agents/src/additional_investment` (`run_additional_investment`) and is reached only through this domain. The AI bridge that *produces* the source allocation lives in `app/domains/ai_engine`.

## Layers
- **models/** — `AdditionalInvestmentRun` (table `additional_investment_runs`) and its children `AdditionalInvestmentTarget` / `AdditionalInvestmentBuy` (added in Plan 3b).
- **schemas/** — the run-API contract (pydantic views over the run tables) including the read-time computed `summary` Invest-page headline (added in Plan 3b).
- **routers/** — the `/additional-investment` router (added in Plan 3b).
- **services/** — `additional_investment_persist_service` (the write surface, called by the ai_engine bridge); `additional_investment_module_service` (the gateway above); `additional_investment_summary` (deterministic Invest-page headline builder); `ainv_engine/` — the compute orchestration (named `ainv_engine` because `ai_engine` is taken) that wraps the pure `AI_Agents.additional_investment` engine.

## Gotchas & invariants
- **Import `chat` lazily.** `ainv_engine/__init__.py` is docstring-only and must NOT re-export `chat` — eager import triggers a circular import via `chat_core.turn_context` (mirrors `rebal_engine`). The `@register("additional_investment")` side-effect is landed by a lazy `from ...ainv_engine import chat` inside `additional_investment_module_service.run`.
- **BUY-only, write-once.** A run only adds new money; there is no sell/status lifecycle and no update-status route (`models/`, Plan 3b).
- **Money is `float`, not `Decimal`.** This domain follows the allocation family (`practical_asset_allocation`), not Rebalancing — floats flow straight into `Numeric(18,2)`; do NOT import `_to_decimal` in the persist service (`services/additional_investment_persist_service.py`, Plan 3b).
- A run always deploys against the persisted practical-allocation run it was derived from — `source_allocation_run_id` (FK to `practical_asset_allocation_runs.id`) is required (Plan 3b).

## Don't read
- `__pycache__/`.
