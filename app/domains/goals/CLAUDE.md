# app/domains/goals/ — financial goals + contributions + holdings

## Layers

- **models/** — `FinancialGoal`, `GoalContribution`, `GoalHolding` + enums.
- **schemas/** — goal create/update/response + contribution and holding payloads.
- **routers/** — `/goals` (goal CRUD, contributions, holdings).
- **services/** — `retirement_sync` and the goal write/read helpers used by `routers/goals_router.py`.
- **services/** — `retirement_sync` — keeps the Retirement goal's target year and the investment profile's `retirement_age` in lockstep; whichever side is edited, the other follows (`sync_retirement_age_from_goal` / `sync_retirement_goal_from_age`).

## Gotchas & invariants

- The cashflow-staleness side-effect lives in the **router**, not the service: every goal create / update / delete calls `mark_cashflow_stale(db, user_id)` so a cached cashflow plan is recomputed (`routers/goals_router.py`). Miss it and goal edits leave a stale plan.

## Don't read

- `__pycache__/`.
