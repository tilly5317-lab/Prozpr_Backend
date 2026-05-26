"""Pydantic enums aligned with cashflow PostgreSQL types."""

from __future__ import annotations

from enum import Enum


class CashflowGoalType(str, Enum):
    retirement = "retirement"
    property = "property"
    child_abroad_education = "child_abroad_education"
    child_local_education = "child_local_education"
    child_marriage = "child_marriage"
    custom = "custom"


class OneOffDirection(str, Enum):
    in_ = "in"
    out = "out"


class InvestmentSource(str, Enum):
    user_sip = "user_sip"
    user_sip_capped = "user_sip_capped"
    savings_sip_fraction = "savings_sip_fraction"
    withdrawal = "withdrawal"
    zero = "zero"


class DetailLevel(str, Enum):
    default = "default"
    full = "full"
