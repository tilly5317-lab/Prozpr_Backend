"""PostgreSQL-aligned enums for cashflow_statement persistence."""

from __future__ import annotations

import enum


class CashflowGoalType(str, enum.Enum):
    retirement = "retirement"
    property = "property"
    child_abroad_education = "child_abroad_education"
    child_local_education = "child_local_education"
    child_marriage = "child_marriage"
    custom = "custom"


class OneOffDirection(str, enum.Enum):
    in_ = "in"
    out = "out"


class InvestmentSource(str, enum.Enum):
    user_sip = "user_sip"
    user_sip_capped = "user_sip_capped"
    savings_sip_fraction = "savings_sip_fraction"
    withdrawal = "withdrawal"
    zero = "zero"


class DetailLevel(str, enum.Enum):
    default = "default"
    full = "full"
