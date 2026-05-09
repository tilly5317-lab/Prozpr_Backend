"""Module constants for goal_planning. No BaseSettings — matches project pattern."""
import os

AGENT_MODEL = os.getenv("GOAL_PLANNING_AGENT_MODEL", "claude-sonnet-4-6")
EXTRACTOR_MODEL = os.getenv("GOAL_PLANNING_EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
RECURSION_LIMIT = int(os.getenv("GOAL_PLANNING_RECURSION_LIMIT", "15"))
USE_CHECKPOINTER = os.getenv("GOAL_PLANNING_USE_CHECKPOINTER", "true").lower() == "true"
CHECKPOINTER_TYPE = os.getenv("GOAL_PLANNING_CHECKPOINTER_TYPE", "postgres")  # "memory" or "postgres"

# Lever search bounds
SIP_MAX_MULTIPLIER = 5.0                      # Lever A
DEFER_MAX_YEARS = 10                          # Lever B
REDUCE_MAX_PCT = 0.50                         # Lever C
STEP_UP_MAX_DELTA_PP = 0.20                   # Lever E
EXPENSE_REDUCE_PCT_LIST = (0.05, 0.10, 0.15)  # Lever F
MORTGAGE_PAYOFF_YEARS_LIST = (1, 3, 5, 10)    # Lever G

# Extractor defaults (2026 India)
DEFAULT_PROPERTY_DOWNPAYMENT_PCT = 20.0
DEFAULT_MORTGAGE_TENURE_YEARS = 20
DEFAULT_MORTGAGE_INTEREST_ANNUAL = 0.085
FUZZY_MATCH_THRESHOLD = 85
