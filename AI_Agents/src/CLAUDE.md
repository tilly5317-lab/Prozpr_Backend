# AI_Agents/src — Module Map

Python package hosting the Prozper AI financial-advisor agents. Each top-level folder is a self-contained agent/module: a LangChain or orchestrator-driven pipeline that consumes a pydantic input model, calls Claude (typically Haiku), and returns a structured pydantic output. Modules are composed externally by the FastAPI layer; within `src/` they are largely peers, with `allocation/` providing shared primitives reused by `portfolio_query/`.

## Child modules

- **allocation/** — Asset-allocation advisor: client profile + fund view → ideal allocation ranges, delta vs. current portfolio, and narrative recommendation. Entry: `orchestrator.py`.
- **goal_based_allocation_pydantic/** — Pure-Python 7-step goal-based allocation pipeline over pydantic models; LLM use is isolated to an optional rationale step. Entry: `pipeline.py`.
- **intent_classifier/** — Classifies a customer question into one of six intents (portfolio_optimisation, goal_planning, stock_advice, portfolio_query, general_market_query, out_of_scope) using Claude Haiku + structured output. Entry: `classifier.py`.
- **market_commentary/** — Scrapes Indian macro indicators and uses Claude to extract a structured `MacroSnapshot`, then generates a markdown commentary document. Entry: `main.py`.
- **portfolio_query/** — Answers client questions about their own portfolio using fund view + client profile + current portfolio, with in-scope/out-of-scope guardrails. Entry: `orchestrator.py`.
- **risk_profiling/** — Deterministic scoring of a client's risk profile (inputs → scores/flags) plus an LLM-generated summary paragraph. Entry: `main.py`.

## Cross-module edges

- `portfolio_query/` imports shared primitives from `allocation/` (`common.llm_client`, `utilities.fund_view_loader`, `skills.executor`, `schemas.client_profile`, `schemas.portfolio`).
- `intent_classifier/` names the `portfolio_query` intent in its prompt but does not import other `src/` modules — it returns a string label and downstream routing is handled outside `src/`.
- `goal_based_allocation_pydantic/` and `risk_profiling/` both consume fields produced by `risk_profiling/` (e.g. `effective_risk_score`, `osi`, `savings_rate_adjustment`) through their `AllocationInput`, but do not import it directly.
- `goal_based_allocation_pydantic/` `AllocationInput` carries a `market_commentary` score block populated from `market_commentary/`.
- All other modules are independent of each other at the Python-import level.

## Conventions

- `models.py` — top-level pydantic input/output schemas for the module.
- `prompts.py` — prompt strings / `ChatPromptTemplate` objects used by the pipeline.
- `main.py` — LCEL-chain modules (`market_commentary`, `risk_profiling`) expose their chain here.
- `orchestrator.py` — class-based orchestrators (`allocation`, `portfolio_query`) expose their top-level class here.
- `references/` — markdown and CSV domain references consumed by prompts (asset-class rules, carve-outs, guardrails, fund mappings). Not product docs.
- `Testing/` — pytest suites and dev sample runners.
- `dev_run.py` — developer smoke-test script; run as `python -m <module>.dev_run` from `src/`.
- Prompt-adjacent `.md` files (e.g. `portfolio_query/portfolio_query.md`, `portfolio_query/guardrails.md`, `allocation/skills/*.md`) are skill/prompt sources loaded at runtime, not documentation.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
- `../archive/` — historical modules, not part of the active pipeline

## Refresh

If this file looks stale after a structural change, run `/refresh-context` from this folder.
