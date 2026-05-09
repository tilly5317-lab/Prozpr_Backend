import pytest
from goal_planning.engine.exceptions import (
    GoalPlanningEngineError, MissingDOBError, PastGoalDateError, RATEConvergenceError,
)


def test_exception_hierarchy():
    assert issubclass(MissingDOBError, GoalPlanningEngineError)
    assert issubclass(PastGoalDateError, GoalPlanningEngineError)
    assert issubclass(RATEConvergenceError, GoalPlanningEngineError)


def test_can_raise_and_catch():
    with pytest.raises(GoalPlanningEngineError):
        raise MissingDOBError("dob missing")
