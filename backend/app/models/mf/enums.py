"""SQLAlchemy ORM model — `enums.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import enum


class MfPlanType(str, enum.Enum):
    DIRECT = "DIRECT"
    REGULAR = "REGULAR"


class MfOptionType(str, enum.Enum):
    GROWTH = "GROWTH"
    IDCW = "IDCW"


class MfTransactionType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_OUT = "SWITCH_OUT"
    DIVIDEND_REINVEST = "DIVIDEND_REINVEST"


class MfTransactionSource(str, enum.Enum):
    AA = "AA"
    SIMBANKS = "SIMBANKS"
    MANUAL = "MANUAL"
    BACKFILL = "BACKFILL"


class MfSipFrequency(str, enum.Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class MfStepupFrequency(str, enum.Enum):
    ANNUALLY = "ANNUALLY"
    HALF_YEARLY = "HALF_YEARLY"


class MfSipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class UserInvestmentListKind(str, enum.Enum):
    ILLIQUID_EXIT = "ILLIQUID_EXIT"
    STCG = "STCG"
    RESTRICTED = "RESTRICTED"


class PortfolioSnapshotKind(str, enum.Enum):
    IDEAL = "IDEAL"
    SUGGESTED = "SUGGESTED"
    ACTUAL = "ACTUAL"


class MfAaImportStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    NORMALIZING = "NORMALIZING"
    NORMALIZED = "NORMALIZED"
    FAILED = "FAILED"
