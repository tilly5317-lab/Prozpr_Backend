# AI_Agents/src — Module Map

Python package hosting the Prozpr AI financial-advisor agents. Each top-level folder is a self-contained agent: a LangChain or orchestrator-driven pipeline over pydantic input → structured pydantic output. The FastAPI layer composes them; within `src/` they are peers and do not import each other (exceptions in **Cross-module edges**).

## Files at this level

- `common.py` — cross-agent utilities; standard-library only, must not import any peer agent. Public exports: `format_inr_indian`, `read_text_bom_aware`, `RISK_CATEGORIES`, `category_for_effective_risk_score`. The `app/` layer re-imports `format_inr_indian` + `RISK_CATEGORIES` + `category_for_effective_risk_score` with NO parallel copy — edit here, app follows (`app/domains/ai_engine/common.py`, `app/domains/profile/models/risk_profile.py`).

## Child modules

- **asset_allocation_pydantic/** — pure-Python goal-based allocation pipeline over pydantic models; LLM use isolated to an optional rationale step. Entry: `pipeline.py`.
- **cashflow_statement/** — goal-planning engine (8-stage pure-Python pipeline) + LangChain agent for NL goal extraction and lever proposal, validated against an Excel-parity baseline. Entry: `engine/pipeline.py`, `agent/graph.py`. See `cashflow_statement/CLAUDE.md`.
- **financial_primitives/** — shared numerical kernel (TVM, annuity, inflation, Indian FY dates, retirement, XIRR). Pure functions, no LLM, no I/O. Library not agent; not an LLM tool. See `financial_primitives/CLAUDE.md`.
- **Rebalancing/** — pure-Python rebalancing engine; takes an ideal allocation + current holdings, emits per-fund target/buy/sell under per-fund caps with tax-aware sell prioritisation. Entry: `pipeline.py`. See `Rebalancing/CLAUDE.md`.
- **intent_classifier/** — classifies a customer question into one of eight intents using Claude Haiku + structured output. Entry: `classifier.py`.
- **market_commentary/** — scrapes Indian macro indicators, extracts a structured `MacroSnapshot` via Claude, then writes a markdown commentary doc to `Reference_docs/`. Entry: `main.py`.
- **portfolio_query/** — answers client questions about their own portfolio from market commentary + client profile + holdings, with in/out-of-scope guardrails. Entry: `orchestrator.py`.
- **practical_asset_allocation/** — holdings-aware allocation. Wraps `asset_allocation_pydantic` with four extra corpus inputs; reimplements long-term with ELSS freeze, non-MF-equity NFA-banded cap, and v2 equity-subgroup slider. Entry: `pipeline.py`. See `practical_asset_allocation/CLAUDE.md`.
- **additional_investment/** — pure-Python engine that deploys fresh money (lumpsum/SIP) into specific funds (BUY-only, holdings-aware); consumes the practical allocation's per-bucket subgroups + goal-funding status, emits a per-fund BUY list. Entry: `pipeline.py`. See `additional_investment/CLAUDE.md`.
- **risk_profiling/** — deterministic scoring of a client's risk profile (inputs → scores/flags) + an LLM-generated summary paragraph. Entry: `main.py`.
- **chat_eval/** — (gitignored; dev-only) eval harness: replays a YAML question set through the chat pipeline, emits JSON/HTML reports. Entry: `run_eval.py`.

> `drift_analysis/` lives in `AI_Agents/archive/`, not here.

## Cross-module edges

- `intent_classifier/` names the `portfolio_query` intent in its prompt but does not import other `src/` modules — it returns a string label; downstream routing is handled outside `src/`.
- `portfolio_query/` reads `AI_Agents/Reference_docs/market_commentary_latest.md` (written by `market_commentary/`) but does not import the `market_commentary` module — the file is the contract.
- `asset_allocation_pydantic/`'s `AllocationInput` carries fields produced by `risk_profiling/` (`effective_risk_score`, `osi`, `savings_rate_adjustment`) but does not import `risk_profiling/` — the caller wires them in.
- `asset_allocation_pydantic/` `AllocationInput` carries a `market_commentary` score block populated from `market_commentary/`.
- `practical_asset_allocation/` imports from `asset_allocation_pydantic/` (steps 1-3, step5, selected step4 helpers, utils, models) — **the first explicit cross-agent import** under `src/`, blessed by spec §B.1. Documented on both sides.
- `Rebalancing/` imports `run_practical_allocation` from `practical_asset_allocation/` (Part C of the same spec); `pipeline.run_rebalancing` calls it first and surfaces its output on `RebalancingComputeResponse.practical_allocation`.
- `additional_investment/` is a pure engine that imports no peer agent — its `AdditionalInvestmentInput` carries data the app-layer adapter lifts from `practical_asset_allocation/` (per-bucket subgroups), `cashflow_statement/` (goal funding → `medium_term_fulfilled`), and the fund-ranking CSV; the models are the contract.
- All other modules are independent of each other at the Python-import level.

## Conventions

- Per-module file roles: `models.py` (pydantic I/O schemas), `prompts.py` (prompt strings / `ChatPromptTemplate`), `main.py` (LCEL chain), `orchestrator.py` (class-based orchestrator's top-level class).
- `references/` — markdown/CSV domain references consumed by prompts (carve-outs, guardrails, fund mappings). Not product docs.
- `Testing/` — pytest suites + sample runners.
- `dev_run.py` — developer smoke-test (`python -m <module>.dev_run`); present only in `portfolio_query`, `risk_profiling`, `cashflow_statement`.
- Prompt-adjacent `.md` files are runtime skill/prompt sources, not docs.
- LLM calls go through LangChain — see root `CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
- `../archive/` — historical modules, not the active pipeline
- `docs/` — local planning scaffolding, not agent code
