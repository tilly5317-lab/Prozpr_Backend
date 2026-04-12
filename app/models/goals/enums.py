"""SQLAlchemy ORM model — `enums.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import enum


class GoalType(str, enum.Enum):
    RETIREMENT = "RETIREMENT"
    CHILD_EDUCATION = "CHILD_EDUCATION"
    HOME_PURCHASE = "HOME_PURCHASE"
    VEHICLE = "VEHICLE"
    WEDDING = "WEDDING"
    TRAVEL = "TRAVEL"
    EMERGENCY_FUND = "EMERGENCY_FUND"
    WEALTH_CREATION = "WEALTH_CREATION"
    OTHER = "OTHER"


class GoalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ACHIEVED = "ACHIEVED"
    PAUSED = "PAUSED"
    ABANDONED = "ABANDONED"


class GoalPriority(str, enum.Enum):
    PRIMARY = "PRIMARY"
    MEDIUM = "MEDIUM"
    SECONDARY = "SECONDARY"
