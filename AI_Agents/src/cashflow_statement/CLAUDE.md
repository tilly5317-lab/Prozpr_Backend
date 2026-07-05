# cashflow_statement/ — Goal-planning engine + agent

Pure-Python financial-planning engine that takes a `GoalPlanningInput` (profile, retirement, properties, custom goals, one-off events) and produces a `GoalPlanningOutput` (headline status, per-goal funding, monthly & annual cashflow projections). A thin LangChain/LangGraph agent wraps the engine for conversational goal extraction and lever proposal.

## Child modules

- **engine/** — 8-stage projection pipeline (profile → retirement → mortgages → properties → goals_table → cashflow → funding → summary), one file per stage. Plus `pipeline.py` (orchestration), `_types.py` (internal types), `dates.py` (FY helpers + ROUND_THOUSAND), `exceptions.py`. Pure-Python; no LLM calls.
- **agent/** — LangGraph agent driving the engine from natural language: `extractor.py` (Claude Haiku structured-output extractor), `graph.py` + `nodes.py` (StateGraph wiring), `state.py`, `tools.py` (engine-invoking tools), `levers.py` (deterministic feasibility levers A–F), `prompts.py`.
- **Testing/** — pytest suites: `unit/` (per-stage), `integration/` (end-to-end), `agent/` (extractor + levers), `boundary/` (public-API surface).

## Files at this level

- `models.py` — all public Pydantic contracts (inputs, outputs, agent types, enums). The single source of truth for the engine↔agent boundary.
- `__init__.py` — public API re-exports; the app layer (cashflow domain services) imports only from here.
- `summarizer.py` — Haiku LCEL chain turning a `GoalPlanningOutput` into a customer-facing `PlanSummary`. Rupee values are pre-formatted to Indian notation; the LLM copies them verbatim, never doing its own arithmetic.
- `dev_run.py` — developer smoke-test; runs the engine on a sample profile and writes `dev_artifacts/data.json` + `data.js`. Run as `python -m cashflow_statement.dev_run` from `src/`.
- `Cash_flow.html` — static HTML viewer for `dev_artifacts/data.js`; end-user display logic, not docs. (`dev_run.py` still prints the old `viewer.html` name.)

## Conventions

- **Time conventions.** All inflation FV math and the PV-discount of corpus to today use **day-precise `EOMONTH(target_date)/365`** (`engine/properties.py`, `engine/goals_table.py`, `engine/retirement.py`) — symmetric inflate/discount across every stage.
- **₹1000 rounding** (`dates._round_thousand`) is applied to all FV cashflow anchors (corpus_required_fv, target_fv). PV/display-only fields stay unrounded.
- **Indian financial year** runs April–March. `fy_for_date` returns the closing year (April 2026 → FY27).
- **One shared corpus pool**, not per-goal. The funding stage walks the monthly cashflow and splits shortfalls proportionally across that month's outflows.
- **Sign conventions**: shortfalls positive, EMI/expense/goal_payout positive magnitudes, `surplus_or_shortfall_today` signed (negative = shortfall).
- **Internal types live in `engine/_types.py`** and are NOT re-exported from `__init__.py`. Cross-boundary types live in `models.py`.
- **LLM calls go through `langchain-anthropic`** — see root `CLAUDE.md`. The only permitted raw `anthropic` import is `from anthropic import APIError` for exception handling.

## Gotchas & invariants

- **The agent submodule loads lazily.** `__init__.py` exposes `cashflow_statement_graph` / `run_cashflow_statement` via `__getattr__`, importing `agent/` (which needs `langgraph`) only on first access — so the pure-Python engine path imports cleanly without `langgraph` installed (`__init__.py`).

## Don't read

- `__pycache__/`, `.pytest_cache/`, `dev_artifacts/` — build/output caches.
- `Cash_flow.html` — end-user display logic; read only if changing the viewer.
