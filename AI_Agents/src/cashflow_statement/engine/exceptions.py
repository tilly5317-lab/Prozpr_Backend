"""Engine exception classes."""
from __future__ import annotations


class GoalPlanningEngineError(Exception):
    """Base class for engine errors."""


class MissingDOBError(GoalPlanningEngineError):
    """Date of birth missing — required for retirement calc."""
