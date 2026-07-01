# app/domains/additional_investment/ — deploy fresh money (lumpsum/SIP) into specific funds to BUY

## Entry / contract
- `additional_investment_module_service` is the ONLY gateway to the additional-investment AI module — the brain calls its `run(turn, ctx, prior)`. The pure engine that picks the funds lives in `AI_Agents/src/additional_investment` (`run_additional_investment`) and is reached only through this domain. The source practical allocation is re-computed inside `ainv_engine/service.py` (via `practical_asset_allocation`'s `compute_practical_allocation_result`) and persisted to yield `source_allocation_run_id`.

## Layers
- **models/** — `AdditionalInvestmentRun` (table `additional_investment_runs`) and its children `AdditionalInvestmentTarget` / `AdditionalInvestmentBuy`.
- **services/** — `additional_investment_module_service` (the gateway above); `additional_investment_persist_service` (the normalized BUY-only write surface); `ainv_engine/` — the compute orchestration (named `ainv_engine` because `ai_engine` is taken) that wraps the pure `AI_Agents.additional_investment` engine and houses the chat handler (deploy-amount + cadence extractor → BUY list).
- **routers/** — placeholder package (only `__init__.py`); the live path is chat via `ChatBrain`, not a REST route.

## Gotchas & invariants
- **Import `chat` lazily.** `ainv_engine/__init__.py` is docstring-only and must NOT re-export `chat` — eager import triggers a circular import via `chat_core.turn_context` (mirrors `rebal_engine`). The `@register("additional_investment")` side-effect is landed by a lazy `from ...ainv_engine import chat` inside `additional_investment_module_service.run`.
- **The deploy-request LLM uses a dedicated key.** Each turn `ainv_engine/chat.py` extracts the deploy amount + lumpsum/SIP cadence from the question via a Haiku `classify_action` call read with `ADDITIONAL_INVESTMENT_API_KEY` (`get_anthropic_additional_investment_key()`), falling back to the deterministic `parse_deploy_request` regex on failure (`ainv_engine/chat.py:163`).
- **BUY-only, write-once.** A run only adds new money; there is no sell/status lifecycle and no update-status route (contrast `RebalancingRun.status`) (`services/additional_investment_persist_service.py`).
- **Money is `float`, not `Decimal`.** This domain follows the allocation family (`practical_asset_allocation`), not Rebalancing — floats flow straight into `Numeric(18,2)`; do NOT import `_to_decimal` in the persist service (`services/additional_investment_persist_service.py`).
- A run always deploys against the persisted practical-allocation run it was derived from — `source_allocation_run_id` (FK to `practical_asset_allocation_runs.id`) is required (`services/additional_investment_persist_service.py`).

## Don't read
- `__pycache__/`, `tests/`.
