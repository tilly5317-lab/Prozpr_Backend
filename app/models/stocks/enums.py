"""SQLAlchemy ORM model — `enums.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import enum


class StockTransactionType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
