"""Pydantic schema — `simbanks.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


SimBankAccountKind = Literal["deposit", "mutual_fund", "equity"]


class SimBankDiscoveredAccount(BaseModel):
    account_ref_no: str
    provider_name: str
    fi_type: str
    account_type: str
    kind: SimBankAccountKind

    masked_identifier: Optional[str] = None
    currency: Optional[str] = None

    current_value: float = Field(..., ge=0)
    cost_value: Optional[float] = None
    holdings_count: Optional[int] = None


class DiscoverSimBankAccountsResponse(BaseModel):
    accounts: list[SimBankDiscoveredAccount]


class SyncSimBankAccountsRequest(BaseModel):
    accepted_account_ref_nos: list[str] = Field(..., min_length=1)


class SyncSimBankAccountsResponse(BaseModel):
    portfolio_total_value: float
    portfolio_total_invested: float
    portfolio_total_gain_percentage: Optional[float] = None
    linked_account_ids: list[uuid.UUID]

