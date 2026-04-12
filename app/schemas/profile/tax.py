"""Pydantic schema — `tax.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TaxProfileUpdate(BaseModel):
    income_tax_rate: Optional[float] = None
    capital_gains_tax_rate: Optional[float] = None
    notes: Optional[str] = None


class TaxProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    income_tax_rate: Optional[float] = None
    capital_gains_tax_rate: Optional[float] = None
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None
