# app/domains/goals/ — financial goals + contributions + holdings

Financial goals + contributions + holdings.

## Layers

- **models/** — FinancialGoal, GoalContribution, GoalHolding + enums
- **schemas/** — GoalCreate / GoalUpdate / GoalResponse / GoalDetailResponse / contribution + holding payloads
- **routers/** — /goals router (CRUD + contributions)
- **services/** — goal_service — CRUD + cashflow staleness side-effect

## Don't read

- `__pycache__/`.
