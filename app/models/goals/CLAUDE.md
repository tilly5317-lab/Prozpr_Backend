# app/models/goals/

Financial goal tables covering goals, periodic contributions, and fund/stock holdings
assigned to each goal. Column-level detail: `README_DATABASE_SCHEMA.md`.

## Files

- `enums.py` — goal-domain enum types (no ORM table)
- `financial_goal.py` — `FinancialGoal`
- `goal_contribution.py` — `GoalContribution`
- `goal_holding.py` — `GoalHolding`

## Tables

- `goals` — `FinancialGoal`; a user's named financial target with horizon and target amount. Relationships: belongs to User; has many GoalContributions, has many GoalHoldings.
- `goal_contributions` — `GoalContribution`; individual contribution events credited toward a goal. Relationships: belongs to FinancialGoal.
- `goal_holdings` — `GoalHolding`; fund or stock positions currently allocated to a goal. Relationships: belongs to FinancialGoal.

## Depends on

- `app/models/user.py` — User hub; `goals` table carries a `users.id` foreign key.

## Don't read

- `__pycache__/`.
