"""Composite input payload — mirrors ``GoalPlanningInput`` for API persistence."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.domains.cashflow.schemas.assumptions import CashflowAssumptionsBase
from app.domains.cashflow.schemas.enums import DetailLevel
from app.domains.cashflow.schemas.goals import CashflowGoalBase
from app.domains.cashflow.schemas.one_off import CashflowOneOffEventBase
from app.domains.profile.schemas.investment import CurrentPropertyItem
from app.domains.profile.schemas.personal import PersonalFinanceFields


class CashflowClientProfile(PersonalFinanceFields):
    """Engine ``ClientProfile`` — reads canonical personal-finance scalars only."""


class CashflowPlanningInput(BaseModel):
    """User-scoped input bundle read when building a plan run."""

    assumptions: CashflowAssumptionsBase
    profile: CashflowClientProfile
    current_properties: list[CurrentPropertyItem] = Field(default_factory=list)
    goals: list[CashflowGoalBase] = Field(default_factory=list)
    one_off_inflows: list[CashflowOneOffEventBase] = Field(default_factory=list)
    one_off_outflows: list[CashflowOneOffEventBase] = Field(default_factory=list)
    detail_level: DetailLevel = DetailLevel.default

    @model_validator(mode="after")
    def _unique_names(self) -> "CashflowPlanningInput":
        names: list[str] = [g.name for g in self.goals]
        names.extend(p.name for p in self.current_properties)
        names.extend(e.description for e in self.one_off_inflows)
        names.extend(e.description for e in self.one_off_outflows)
        normalized = [n.casefold() for n in names]
        dupes = {n for n in normalized if normalized.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate names across inputs (case-insensitive): {sorted(dupes)}")
        return self
