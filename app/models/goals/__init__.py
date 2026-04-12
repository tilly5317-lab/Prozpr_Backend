"""SQLAlchemy ORM model — `__init__.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from app.models.goals.enums import GoalPriority, GoalStatus, GoalType
from app.models.goals.financial_goal import FinancialGoal
from app.models.goals.goal_contribution import GoalContribution
from app.models.goals.goal_holding import GoalHolding

__all__ = [
    "FinancialGoal",
    "GoalContribution",
    "GoalHolding",
    "GoalPriority",
    "GoalStatus",
    "GoalType",
]
