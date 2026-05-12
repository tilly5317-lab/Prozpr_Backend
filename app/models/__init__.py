"""SQLAlchemy ORM model — `__init__.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from app.models.chat import ChatMessage, ChatSession
from app.models.chat_ai_module_run import ChatAiModuleRun
from app.models.family_member import FamilyMember
from app.models.fund import Fund
from app.models.goals import (
    AllocationBucketName,
    AssetClassSplitKind,
    FinancialGoal,
    GoalAllocationBucket,
    GoalAllocationBucketAssetClass,
    GoalAllocationBucketGoal,
    GoalAllocationBucketSubgroup,
    GoalAllocationGoal,
    GoalAllocationRun,
    GoalAllocationRunStatus,
    GoalContribution,
    GoalHolding,
)
from app.models.ips import InvestmentPolicyStatement
from app.models.linked_account import LinkedAccount
from app.models.meeting_note import MeetingNote, MeetingNoteItem
from app.models.mf import (
    MfFundMetadata,
    MfFundRating,
    MfNavHistory,
    MfSipMandate,
    MfTransaction,
    PortfolioAllocationSnapshot,
    UserMfLatestSnapshot,
    UserInvestmentList,
)
from app.models.notification import Notification
from app.models.portfolio import Portfolio, PortfolioAllocation, PortfolioHolding, PortfolioHistory
from app.models.rebalancing import (
    RebalancingFundRow,
    RebalancingRun,
    RebalancingRunStatus,
    RebalancingSubgroupSummary,
    RebalancingTotals,
    RebalancingTrade,
    RebalancingWarning,
    RebalancingWarningCode,
    TaxRegime,
    TradeAction,
    TradeExecutionStatus,
)
from app.models.stocks import CompanyMetadata, StockPriceHistory, StockTransaction
from app.models.user import User
from app.models.profile import (
    AssetAllocationConstraint,
    EffectiveRiskAssessment,
    InvestmentConstraint,
    InvestmentProfile,
    OtherInvestment,
    ReviewPreference,
    RiskProfile,
    TaxProfile,
    PersonalFinanceProfile,
)

__all__ = [
    "AllocationBucketName",
    "AssetAllocationConstraint",
    "AssetClassSplitKind",
    "EffectiveRiskAssessment",
    "ChatAiModuleRun",
    "ChatMessage",
    "ChatSession",
    "CompanyMetadata",
    "FamilyMember",
    "FinancialGoal",
    "Fund",
    "GoalAllocationBucket",
    "GoalAllocationBucketAssetClass",
    "GoalAllocationBucketGoal",
    "GoalAllocationBucketSubgroup",
    "GoalAllocationGoal",
    "GoalAllocationRun",
    "GoalAllocationRunStatus",
    "GoalContribution",
    "GoalHolding",
    "InvestmentConstraint",
    "InvestmentPolicyStatement",
    "InvestmentProfile",
    "LinkedAccount",
    "MeetingNote",
    "MeetingNoteItem",
    "MfFundMetadata",
    "MfFundRating",
    "MfNavHistory",
    "MfSipMandate",
    "MfTransaction",
    "Notification",
    "OtherInvestment",
    "PersonalFinanceProfile",
    "Portfolio",
    "PortfolioAllocation",
    "PortfolioAllocationSnapshot",
    "PortfolioHistory",
    "PortfolioHolding",
    "RebalancingFundRow",
    "RebalancingRun",
    "RebalancingRunStatus",
    "RebalancingSubgroupSummary",
    "RebalancingTotals",
    "RebalancingTrade",
    "RebalancingWarning",
    "RebalancingWarningCode",
    "ReviewPreference",
    "RiskProfile",
    "StockPriceHistory",
    "StockTransaction",
    "TaxProfile",
    "TaxRegime",
    "TradeAction",
    "TradeExecutionStatus",
    "User",
    "UserInvestmentList",
    "UserMfLatestSnapshot",
]
