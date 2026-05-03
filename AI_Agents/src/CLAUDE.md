# AI_Agents/src — Module Map

Python package hosting the Prozpr AI financial-advisor agents. Each top-level folder is a self-contained agent/module: a LangChain or orchestrator-driven pipeline that consumes a pydantic input model, calls Claude (typically Haiku), and returns a structured pydantic output. Modules are composed externally by the FastAPI layer; within `src/` they are peers and do not import each other (with the documented exceptions in **Cross-module edges** below).

## Files at this level

- `common.py` — cross-agent utilities (e.g., `format_inr_indian` for Indian-notation rupee formatting). Self-contained; depends only on the standard library. Other modules under `AI_Agents/src/` may import from this file freely; this file must not import from any peer agent module. The `app/` layer re-exports from here via `app/services/ai_bridge/common.py`.

## Child modules

- **asset_allocation_pydantic/** — Pure-Python goal-based allocation pipeline over pydantic models; LLM use is isolated to an optional rationale step. Entry: `pipeline.py`.
- **Rebalancing/** — Pure-Python rebalancing engine; takes a goal-based ideal allocation plus current holdings and emits per-fund target / buy / sell amounts under per-fund caps with tax-aware sell prioritisation. Entry: `pipeline.py`.
- **intent_classifier/** — Classifies a customer question into one of six intents (asset_allocation, goal_planning, stock_advice, portfolio_query, general_market_query, out_of_scope) using Claude Haiku + structured output. Entry: `classifier.py`.
- **market_commentary/** — Scrapes Indian macro indicators and uses Claude to extract a structured `MacroSnapshot`, then generates a markdown commentary document persisted to `AI_Agents/Reference_docs/`. Entry: `main.py`.
- **portfolio_query/** — Self-contained agent that answers client questions about their own portfolio using market commentary + client profile + current portfolio (asset-class, sub-category, and per-fund detail), with in-scope/out-of-scope guardrails. Entry: `orchestrator.py`.
- **risk_profiling/** — Deterministic scoring of a client's risk profile (inputs → scores/flags) plus an LLM-generated summary paragraph. Entry: `main.py`.
- **chart_selector/** — Tool-forced Claude Haiku agent that picks a relevant subset of charts from a caller-supplied catalogue. Entry: `selector.py`.
- **router/** — STUB; only `README.md`. No Python modules yet — placeholder for a future routing agent.

> Note: `drift_analysis/` lives in `AI_Agents/archive/drift_analysis/`, not here.

## Cross-module edges

- `intent_classifier/` names the `portfolio_query` intent in its prompt but does not import other `src/` modules — it returns a string label and downstream routing is handled outside `src/`.
- `portfolio_query/` reads `AI_Agents/Reference_docs/market_commentary_latest.md` (written by `market_commentary/`) but does not import the `market_commentary` module — the file is the contract.
- `asset_allocation_pydantic/`'s `AllocationInput` carries fields produced by `risk_profiling/` (`effective_risk_score`, `osi`, `savings_rate_adjustment`) but does not import `risk_profiling/` directly — the caller wires them in.
- `asset_allocation_pydantic/` `AllocationInput` carries a `market_commentary` score block populated from `market_commentary/`.
- All other modules are independent of each other at the Python-import level.

## Conventions

- `models.py` — top-level pydantic input/output schemas for the module.
- `prompts.py` — prompt strings / `ChatPromptTemplate` objects used by the pipeline.
- `main.py` — LCEL-chain modules (`market_commentary`, `risk_profiling`) expose their chain here.
- `orchestrator.py` — class-based orchestrators (`portfolio_query`) expose their top-level class here.
- `references/` — markdown and CSV domain references consumed by prompts (asset-class rules, carve-outs, guardrails, fund mappings). Not product docs.
- `Testing/` — pytest suites and dev sample runners.
- `dev_run.py` — developer smoke-test script; run as `python -m <module>.dev_run` from `src/`.
- Prompt-adjacent `.md` files (e.g. `portfolio_query/portfolio_query.md`, `portfolio_query/guardrails.md`) are skill/prompt sources loaded at runtime, not documentation.
- LLM calls go through `langchain-anthropic` (`ChatAnthropic` / LCEL). No raw `anthropic` SDK calls — see root `CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
- `../archive/` — historical modules, not part of the active pipeline
- `docs/` — local planning scaffolding, not agent code
