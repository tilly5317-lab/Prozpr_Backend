"""Goals package — canonical user goals + goal-based allocation pipeline output.

Two distinct concepts live here:

- ``FinancialGoal`` (``goals`` table) and friends — the user's persistent
  goals as edited from the app. Supporting children: ``GoalContribution``,
  ``GoalHolding``.
- ``GoalAllocationRun`` and children — output of one execution of the
  ``asset_allocation_pydantic`` pipeline. A run snapshots the goals it saw
  (``GoalAllocationGoal``) so the recommendation stays interpretable even
  after the canonical goal is edited or deleted.
"""


from app.models.goals.enums import GoalPriority, GoalStatus, GoalType
from app.models.goals.financial_goal import FinancialGoal
from app.models.goals.goal_allocation_bucket import (
    AllocationBucketName,
    AssetClassSplitKind,
    GoalAllocationBucket,
    GoalAllocationBucketAssetClass,
    GoalAllocationBucketGoal,
    GoalAllocationBucketSubgroup,
)
from app.models.goals.goal_allocation_run import (
    GoalAllocationGoal,
    GoalAllocationRun,
    GoalAllocationRunStatus,
)
from app.models.goals.goal_contribution import GoalContribution
from app.models.goals.goal_holding import GoalHolding

__all__ = [
    "AllocationBucketName",
    "AssetClassSplitKind",
    "FinancialGoal",
    "GoalAllocationBucket",
    "GoalAllocationBucketAssetClass",
    "GoalAllocationBucketGoal",
    "GoalAllocationBucketSubgroup",
    "GoalAllocationGoal",
    "GoalAllocationRun",
    "GoalAllocationRunStatus",
    "GoalContribution",
    "GoalHolding",
    "GoalPriority",
    "GoalStatus",
    "GoalType",
]
