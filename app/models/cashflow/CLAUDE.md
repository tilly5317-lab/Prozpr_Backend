# app/models/cashflow/

Persistence for the cashflow-statement / goal-planning engine. Mirrors the
public Pydantic contracts in `AI_Agents/src/cashflow_statement/models.py`;
column-level spec lives in `viewer_db_schema.md` at the repo root.

User-scoped inputs are keyed by `user_id` (one row per user for 1:1 tables,
many for line-item tables). Engine outputs hang off `cashflow_plan_runs` and
cascade-delete with the run row.

## Files

- `enums.py` — domain enum types (`GoalTypeCashflow`, `OneOffDirection`,
  `InvestmentSource`, `DetailLevel`); no ORM table.
- `assumption.py` — `CashflowInputAssumption`
- `one_off_event.py` — `CashflowInputOneOffEvent`
- `plan_run.py` — `CashflowPlanRun`
- `annual_row.py` — `CashflowAnnualRow`
- `monthly_row.py` — `CashflowMonthlyRow`
- `headline.py` — `CashflowHeadline`
- `fund_flow_summary.py` — `CashflowFundFlowSummary`
- `plan_summary.py` — `CashflowPlanSummary`

## Tables

- `cashflow_input_assumptions` — `CashflowInputAssumption`; per-user inflation
  / ROI / mortgage default assumptions. Relationships: belongs to User (PK is
  `user_id`).
- `cashflow_input_one_off_events` — `CashflowInputOneOffEvent`; per-user
  one-off inflow / outflow line items with explicit direction. Relationships:
  belongs to User.
- `cashflow_plan_runs` — `CashflowPlanRun`; one row per engine invocation;
  parent of every output table below. Relationships: belongs to User
  (nullable for dev / anonymous runs), optionally belongs to ChatSession; has
  many CashflowAnnualRows / CashflowMonthlyRows; has one CashflowHeadline /
  CashflowFundFlowSummary / CashflowPlanSummary.
- `cashflow_annual_rows` — `CashflowAnnualRow`; per-FY rollup of the P&L and
  corpus evolution. Relationships: belongs to CashflowPlanRun.
- `cashflow_monthly_rows` — `CashflowMonthlyRow`; per-month detail row,
  persisted only when the run used `detail_level = 'full'`. Relationships:
  belongs to CashflowPlanRun.
- `cashflow_headline` — `CashflowHeadline`; per-run headline status (1:1 with
  the plan run). Relationships: belongs to CashflowPlanRun.
- `cashflow_fund_flow_summary` — `CashflowFundFlowSummary`; per-run horizon
  bridge + present-value goal-funding snapshot (1:1 with the plan run).
  Relationships: belongs to CashflowPlanRun.
- `cashflow_plan_summary` — `CashflowPlanSummary`; per-run LLM narrative
  payload with nested JSONB lists (1:1 with the plan run). Relationships:
  belongs to CashflowPlanRun.

## Depends on

- `app/models/user.py` — User hub; every cashflow input table and most
  outputs carry a `users.id` foreign key.
- `app/models/chat.py` — `cashflow_plan_runs.chat_session_id` references
  `chat_sessions(id)` for agent-driven runs.
- `AI_Agents/src/cashflow_statement/models.py` — Pydantic source of truth for
  field names and semantics.

## Don't read

- `__pycache__/`.
