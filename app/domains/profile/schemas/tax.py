"""Pydantic schemas for TaxProfile read/update."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TaxProfileUpdate(BaseModel):
    income_tax_rate: Optional[float] = None
    capital_gains_tax_rate: Optional[float] = None
    notes: Optional[str] = None
    tax_regime: Optional[str] = None
    carryforward_st_loss_inr: Optional[float] = None
    carryforward_lt_loss_inr: Optional[float] = None


class TaxProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    income_tax_rate: Optional[float] = None
    capital_gains_tax_rate: Optional[float] = None
    notes: Optional[str] = None
    tax_regime: Optional[str] = None
    carryforward_st_loss_inr: float = 0
    carryforward_lt_loss_inr: float = 0
