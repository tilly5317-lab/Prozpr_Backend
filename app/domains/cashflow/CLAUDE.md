# app/domains/cashflow/ — cashflow plan engine: per-user assumptions + one-off events + per-run outputs (plan run + headline + annual / monthly rows + fund flow + summary)

Cashflow plan engine: per-user assumptions + one-off events + per-run outputs (plan run + headline + annual / monthly rows + fund flow + summary).

## Layers

- **models/** — CashflowInputAssumptions, CashflowOneOffEvent, CashflowPlanRun + child rows + enums
- **schemas/** — assumptions / goals / input / one_off / outputs / enums payloads
- **routers/** — /cashflow router
- **services/** — cashflow_compute_service (delegates to AI_Agents.cashflow_statement) + cashflow_persist_service

## Don't read

- `__pycache__/`.
