"""Engine exception classes — used by validate_input_only (pre-flight) and engine internals."""
from __future__ import annotations


class GoalPlanningEngineError(Exception):
    """Base class for engine errors."""


class MissingDOBError(GoalPlanningEngineError):
    """Date of birth missing — required for retirement calc."""


class PastGoalDateError(GoalPlanningEngineError):
    """Goal date is on or before latest_update_date.

    Raised by validate_input_only (strict pre-flight); runtime engine drops with warning.
    """


class RATEConvergenceError(GoalPlanningEngineError):
    """RATE inversion did not converge for an existing mortgage.

    Caught internally; warning emitted; fallback to assumptions.default_mortgage_interest_annual.
    """
