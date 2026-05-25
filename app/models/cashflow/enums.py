"""Enum types for the cashflow-statement domain.

Mirrors the categorical fields from `AI_Agents/src/cashflow_statement/models.py`
(`GoalType`, `OneOffEvent.direction`, `MonthlyCashflowRow.investment_source`,
`GoalPlanningInput.detail_level`). PostgreSQL ENUM types are created with the
explicit names declared below so they remain stable across migrations.
"""


from __future__ import annotations

import enum


class GoalTypeCashflow(str, enum.Enum):
    RETIREMENT = "retirement"
    PROPERTY = "property"
    CHILD_ABROAD_EDUCATION = "child_abroad_education"
    CHILD_LOCAL_EDUCATION = "child_local_education"
    CHILD_MARRIAGE = "child_marriage"
    CUSTOM = "custom"


class OneOffDirection(str, enum.Enum):
    IN = "in"
    OUT = "out"


class InvestmentSource(str, enum.Enum):
    USER_SIP = "user_sip"
    USER_SIP_CAPPED = "user_sip_capped"
    SAVINGS_SIP_FRACTION = "savings_sip_fraction"
    WITHDRAWAL = "withdrawal"
    ZERO = "zero"


class DetailLevel(str, enum.Enum):
    DEFAULT = "default"
    FULL = "full"
