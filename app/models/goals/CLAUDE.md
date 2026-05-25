# app/models/goals/

Financial goal tables covering goals, periodic contributions, and fund/stock holdings
assigned to each goal. Column-level detail: `README_DATABASE_SCHEMA.md`.

## Files

- `enums.py` — goal-domain enum types (no ORM table)
- `financial_goal.py` — `FinancialGoal`
- `goal_contribution.py` — `GoalContribution`
- `goal_holding.py` — `GoalHolding`

## Tables

- `goals` — `FinancialGoal`; a user's named financial target. Single canonical row carrying both the legacy onboarding shape (`goal_name`, `present_value_amount`, `target_date`, `priority`, `status`) and the cashflow-engine columns merged in from `viewer_db_schema.md` (`name`, `goal_type_cashflow` enum, `goal_date`, `goal_value_pv` / `goal_value_fv`, `target_pv` / `target_fv`, mortgage fields, `date_of_birth` for retirement). New cashflow columns are nullable, and per-type `CHECK` constraints only fire when `goal_type_cashflow` is set, so legacy rows remain valid. Relationships: belongs to User; has many GoalContributions, has many GoalHoldings.
- `goal_contributions` — `GoalContribution`; individual contribution events credited toward a goal. Relationships: belongs to FinancialGoal.
- `goal_holdings` — `GoalHolding`; fund or stock positions currently allocated to a goal. Relationships: belongs to FinancialGoal.

## Depends on

- `app/models/user.py` — User hub; `goals` table carries a `users.id` foreign key.

## Don't read

- `__pycache__/`.
