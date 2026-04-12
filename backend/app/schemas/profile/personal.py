"""Pydantic schema — `personal.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PersonalInfoUpdate(BaseModel):
    occupation: Optional[str] = None
    family_status: Optional[str] = None
    wealth_sources: Optional[list[str]] = None
    personal_values: Optional[list[str]] = None
    address: Optional[str] = None
    currency: Optional[str] = None


class PersonalInfoResponse(BaseModel):
    model_config = {"from_attributes": True}

    occupation: Optional[str] = None
    family_status: Optional[str] = None
    wealth_sources: Optional[list[str]] = None
    personal_values: Optional[list[str]] = None
    address: Optional[str] = None
    currency: str = "GBP"
