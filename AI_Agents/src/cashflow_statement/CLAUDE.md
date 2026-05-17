# cashflow_statement/ — Goal-planning engine + agent

Pure-Python financial-planning engine that takes a `GoalPlanningInput` (profile, retirement, properties, custom goals, one-off events) and produces a `GoalPlanningOutput` (headline status, per-goal funding, monthly & annual cashflow projections). A thin LangChain agent wraps the engine for conversational goal extraction and lever proposal. Designed for Excel parity against a baseline workbook in `tests/fixtures/excel_reference/`.

## Child modules

- **engine/** — 8-stage projection pipeline (profile → retirement → mortgages → properties → goals_table → cashflow → funding → summary). One file per stage, plus `pipeline.py` (orchestration), `_types.py` (internal pydantic types not exposed to callers), `dates.py` (FY helpers + ROUND_THOUSAND), `exceptions.py`. The pipeline is pure-Python; no LLM calls.
- **agent/** — LangChain/LangGraph agent that drives the engine from natural language. `extractor.py` (Claude Haiku structured-output extractor for goals / properties / cashflow events / mutations), `graph.py` + `nodes.py` (StateGraph wiring), `state.py` (graph state), `tools.py` (engine-invoking tools), `levers.py` (deterministic feasibility levers A/B/C/D/E/F), `prompts.py`.
- **tests/** — pytest suites: `unit/` (per-stage), `integration/` (end-to-end and Excel parity), `agent/` (extractor + levers), `boundary/` (public-API surface), `fixtures/excel_reference/` (baseline workbook snapshot + emulator + cell-mapping doc + parity report).
- **dev_artifacts/** — runtime outputs from `dev_run.py` (`data.json`, `data.js`). Not committed.

## Files at this level

- `models.py` — all public Pydantic contracts (inputs, outputs, agent types, enums). The single source of truth for the engine↔agent boundary.
- `__init__.py` — public API re-exports. The bridge layer imports only from here.
- `dev_run.py` — developer smoke-test that runs the engine on a rich sample profile and writes `dev_artifacts/data.json` + `data.js` for the viewer. Run as `python -m cashflow_statement.dev_run` from `src/`.
- `viewer.html` — static, self-contained HTML viewer that loads `dev_artifacts/data.js` and renders every output section (inputs, retirement, goals, cashflow, fund-flow summary, etc.) as Indian-format tables. Open directly in a browser after `dev_run.py`.
- `summarizer.py` — Haiku-driven LCEL chain that turns a `GoalPlanningOutput` into a customer-facing `PlanSummary`. All rupee values are pre-formatted to Indian notation before reaching the LLM (the model copies them verbatim — never does its own arithmetic).

## Conventions

- **Two time conventions, intentionally separate.** Inflation FV uses **integer FY-year diff** (`dates.fy_years_between`). PV-discount of corpus to today (`engine/goals_table._fund_today_pv`) uses **day-precise `EOMONTH(goal_date)/365`** to match Excel's headline cells O113 / S105. Retirement inflation uses **calendar-year diff** (`engine/retirement.py`) — also Excel-parity.
- **Excel parity is the source of truth.** When engine math diverges from `tests/fixtures/excel_reference/excel_emulator.py`, the emulator wins unless documented as an intentional improvement in `cell_mapping.md`.
- **₹1000 rounding** (`dates._round_thousand`) is applied to all FV cashflow anchors (corpus_required_fv, target_fv). PV/display-only fields stay unrounded.
- **Indian financial year** runs April–March. `fy_for_date` returns the closing year (April 2026 → FY27).
- **One shared corpus pool**, not per-goal. The funding stage walks the monthly cashflow and splits shortfalls proportionally across that month's outflows.
- **Sign conventions**: shortfalls positive, EMI/expense/goal_payout positive magnitudes, `surplus_or_shortfall_today` signed (negative = shortfall).
- **LLM calls go through `langchain-anthropic`** — see root `CLAUDE.md`. The only permitted raw `anthropic` import is `from anthropic import APIError` for exception handling.
- **Internal types live in `engine/_types.py`** and are NOT re-exported from `__init__.py`. Cross-boundary types live in `models.py`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `dev_artifacts/` — build/output caches.
- `viewer.html` is end-user-facing display logic — read only if changing the viewer.
- `tests/fixtures/excel_reference/baseline/*.json` are large snapshot fixtures, not source.
