# app/domains/goals/ — financial goals + contributions + holdings

## Layers

- **models/** — `FinancialGoal`, `GoalContribution`, `GoalHolding` + enums.
- **schemas/** — goal create/update/response + contribution and holding payloads.
- **routers/** — `/goals` (goal CRUD, contributions, holdings).
- **services/** — `goal_service` — read helpers only (`get_user_goals`, `calculate_goal_progress`).

## Gotchas & invariants

- The cashflow-staleness side-effect lives in the **router**, not the service: every goal create / update / delete calls `mark_cashflow_stale(db, user_id)` so a cached cashflow plan is recomputed (`routers/goals_router.py`). Miss it and goal edits leave a stale plan.

## Don't read

- `__pycache__/`.
