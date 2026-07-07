# app/domains/additional_investment/ — deploy fresh money (lumpsum/SIP) into specific funds to BUY

## Entry / contract
- `additional_investment_module_service` is the ONLY gateway to the additional-investment AI module — the brain calls its `run(turn, ctx, prior)`. The pure engine that picks the funds lives in `AI_Agents/src/additional_investment` (`run_additional_investment`) and is reached only through this domain. The source practical allocation is re-computed inside `ainv_engine/service.py` (via `compute_practical_allocation_result`) and persisted to yield `source_allocation_run_id`; for lumpsum it is pinned to actual holdings + fresh money via `CorpusPin`.

## Layers
- **models/** — `AdditionalInvestmentRun` (table `additional_investment_runs`) and its children `AdditionalInvestmentTarget` / `AdditionalInvestmentBuy`.
- **services/** — `additional_investment_module_service` (the gateway above); `additional_investment_persist_service` (the normalized BUY-only write surface); `ainv_engine/` — the compute orchestration (named `ainv_engine` because `ai_engine` is taken): `service.py` (outcome assembly), `input_builder.py` (engine input, `deficit_mode` switch), `holdings_snapshot.py` (current holdings by subgroup), `category.py` (category resolution + status), `chat.py` (chat handler: extractor + formatter bodies).
- **routers/** — placeholder package (only `__init__.py`); the live path is chat via `ChatBrain`, not a REST route.

## Gotchas & invariants
- **Import `chat` lazily.** `ainv_engine/__init__.py` is docstring-only and must NOT re-export `chat` — eager import triggers a circular import via `chat_core.turn_context` (mirrors `rebal_engine`). The `@register("additional_investment")` side-effect is landed by a lazy `from ...ainv_engine import chat` inside `additional_investment_module_service.run`.
- **The deploy-request LLM extracts amount + cadence + optional category.** Each turn `ainv_engine/chat.py` runs a Haiku `classify_action` call on a dedicated key (`get_anthropic_additional_investment_key()`) pulling deploy amount, lumpsum/SIP cadence, and `focus_category`, falling back to the deterministic `parse_deploy_request` regex on failure (`ainv_engine/chat.py`).
- **Lumpsum runs deficit-fill against real holdings** (`ainv_engine/service.py`, `AINV_ENGINE_VERSION = "ainv-3.2.0"`). One `HoldingsSnapshot` supplies both the `CorpusPin` that pins PAA at holdings + deploy (total/MF/non-MF-equity/ELSS corpus) and the engine's `current_value_by_subgroup`; per-subgroup ideal/current/gap/buy facts surface as `outcome.deficit_facts` for the formatter. SIP keeps the legacy profile-corpus path. No snapshot fallback by product decision (CAMS upload mandatory) — a snapshot failure propagates.
- **SIP mirrors the latest rebalancing run** (spec 2026-07-05). `ainv_engine/service.py` reads BUY ISINs via `rebalancing_read_service.latest_buy_trades_by_subgroup` (acting user; run status ignored — product call: all plans treated as accepted); enhancement never gate — a read failure logs and degrades to the engine's rank-1 fallback, never blocks the reply. `sip_rebal_run_id` (str, never raw UUID — JSONB json.dumps) lands in `request_extras` only when a run actually sourced funds.
- **A category ask adds a facts block, not a route** (`ainv_engine/category.py`). `resolve_category` canonicalises the customer's words against the ranking's actual `sub_category` values; `top_funds_for_category` + `category_status` build the `category_ask` facts block the formatter narrates per status — the status vocabulary is contractual.
- **BUY-only, write-once.** A run only adds new money; no sell/status lifecycle, no update-status route (contrast `RebalancingRun.status`) (`services/additional_investment_persist_service.py`).
- **Money is `float`, not `Decimal`.** This domain follows the allocation family (`practical_asset_allocation`), not Rebalancing — floats flow straight into `Numeric(18,2)`; do NOT import `_to_decimal` in the persist service (`services/additional_investment_persist_service.py`).
- A run always deploys against the persisted practical-allocation run it was derived from — `source_allocation_run_id` (FK to `practical_asset_allocation_runs.id`) is required (`services/additional_investment_persist_service.py`).

## Don't read
- `__pycache__/`, `tests/`, `services/ainv_engine/tests/`.
