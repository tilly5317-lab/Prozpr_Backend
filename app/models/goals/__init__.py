"""Goals package — canonical user goals.

This package contains ``FinancialGoal`` (``goals`` table) and friends — the user's persistent
  goals as edited from the app. Now also carries the columns the
  asset-allocation pipeline reads (``time_to_goal_months``, ``amount_needed``,
  ``goal_priority``, ``investment_goal``). Supporting children:
  ``GoalContribution``, ``GoalHolding``.
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
