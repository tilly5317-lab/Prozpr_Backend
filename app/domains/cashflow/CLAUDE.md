# app/domains/cashflow/ — cashflow plan: per-user assumptions + one-off events + persisted plan runs (headline, annual/monthly rows, fund flow, summary)

## Entry / contract
- `cashflow_module_service.run(turn, ctx, prior)` is the ONLY gateway to the cashflow AI module — the brain calls it for the `goal_planning` intent. The `cashflow_statement` engine lives under `AI_Agents/src/`, reached via `services/goal_planning_engine/`.

## Layers
- **models/** — `CashflowInputAssumptions`, `CashflowOneOffEvent`, `CashflowPlanRun` + child rows (`CashflowAnnualRow` / `CashflowMonthlyRow` / `CashflowHeadline` / `CashflowFundFlowSummary` / `CashflowPlanSummary`) + enums.
- **schemas/** — request/response payloads: `input` + `assumptions` + `goals` + `one_off` (write surface), `outputs` (plan-run views), `readiness` (`GET /cashflow/readiness`).
- **routers/** — the `/cashflow` router.
- **services/** — `cashflow_module_service` (the gateway above); `cashflow_compute_service` (`run_cashflow_projection_for_user`, a non-chat engine entry); `cashflow_persist_service`; `goal_planning_engine/` — the chat bridge + engine wiring, documented in its own `goal_planning_engine/CLAUDE.md`.

## Gotchas & invariants
- **Incomplete profiles are HARD-REFUSED, never zero-filled.** The input builder raises `missing_required_inputs:<keys>` (or `missing_date_of_birth`) so the engine only ever runs on the user's real numbers — no default/placeholder substitution (`services/goal_planning_engine/input_builder.py`).
- **The gateway can open the `awaiting_save` multi-turn gate.** When set, the next turn routes back to `goal_planning` regardless of classifier verdict; the flag persists on `chat_session_state` (owned by `ai_engine`), not echoed through `side_effects` (`services/cashflow_module_service.py`).
- **The input builder is synchronous** — it returns the constructed `GoalPlanningInput` + a debug dict directly. Don't await it; only the engine run is offloaded via `asyncio.to_thread` (`services/goal_planning_engine/service.py`).

## Don't read
- `__pycache__/`.
