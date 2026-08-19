"""What the customer's profile is missing, and what each gap blocks.

One truth for three consumers: the chat gate (``ai_engine.planning_gate``), the
``GET /profile/completeness`` endpoint that ``/profile/complete`` renders from,
and the planning flow deciding which question to ask next.

Reads are dispatched on ``FieldSpec.table`` so the registry stays the only
place a column name appears. A value counts as PRESENT when it is not None and
not an empty string; zero is a real answer (a customer with no loans answers 0)
and must never be treated as missing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models.user import User
from app.domains.profile.models.investment_profile import InvestmentProfile
from app.domains.profile.models.personal_finance_profile import PersonalFinanceProfile
from app.domains.profile.models.risk_profile import RiskProfile
from app.domains.profile.models.tax_profile import TaxProfile
from app.domains.profile.services.profile_field_registry import (
    FIELD_REGISTRY,
    FieldSpec,
    NEVER_GATED_INTENTS,
    requirement_for,
    specs_for,
)

logger = logging.getLogger(__name__)

_SECTION_LABELS = {
    "money_map": "Your money map",
    "goals": "What you're working towards",
    "risk_behaviour": "How you invest",
    "tax_details": "Tax",
    "personal": "About you",
}


@dataclass(frozen=True)
class ProfileSnapshot:
    """Current value of every registry field for one user."""

    values: dict[str, Any]
    selected_goals: list[str]

    def has(self, key: str) -> bool:
        v = self.values.get(key)
        if v is None:
            return False
        if isinstance(v, str) and not v.strip():
            return False
        return True

    def missing(self, keys: tuple[str, ...]) -> list[str]:
        return [k for k in keys if not self.has(k)]


async def load_snapshot(db: AsyncSession, user_id: uuid.UUID) -> ProfileSnapshot:
    """Every registry field's current value, in one pass per owning table."""
    rows: dict[str, Any] = {
        "users": (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none(),
        "personal_finance_profiles": (
            await db.execute(
                select(PersonalFinanceProfile).where(
                    PersonalFinanceProfile.user_id == user_id
                )
            )
        ).scalar_one_or_none(),
        "investment_profiles": (
            await db.execute(
                select(InvestmentProfile).where(InvestmentProfile.user_id == user_id)
            )
        ).scalar_one_or_none(),
        "risk_profiles": (
            await db.execute(select(RiskProfile).where(RiskProfile.user_id == user_id))
        ).scalar_one_or_none(),
        "tax_profiles": (
            await db.execute(select(TaxProfile).where(TaxProfile.user_id == user_id))
        ).scalar_one_or_none(),
    }

    values: dict[str, Any] = {}
    for key, fs in FIELD_REGISTRY.items():
        row = rows.get(fs.table)
        values[key] = getattr(row, fs.column, None) if row is not None else None

    pfp = rows.get("personal_finance_profiles")
    raw_goals = getattr(pfp, "selected_goals", None) or []
    selected_goals = [str(g) for g in raw_goals if g]

    return ProfileSnapshot(values=values, selected_goals=selected_goals)


def wants_retirement(snapshot: ProfileSnapshot) -> bool:
    """True when the customer picked a retirement goal — the one condition that
    promotes ``retirement_age`` from optional to required."""
    return any("retire" in g.lower() for g in snapshot.selected_goals)


@dataclass(frozen=True)
class IntentGaps:
    """What ``intent`` is missing for this customer."""

    intent: str
    hard_missing: list[str]
    soft_missing: list[str]

    @property
    def blocked(self) -> bool:
        return bool(self.hard_missing)


def gaps_for_intent(intent: str, snapshot: ProfileSnapshot) -> IntentGaps:
    """Hard and soft gaps for one intent. Never-gated intents return nothing —
    a portfolio or market question is answerable with an empty profile."""
    if intent in NEVER_GATED_INTENTS:
        return IntentGaps(intent=intent, hard_missing=[], soft_missing=[])

    req = requirement_for(intent)
    hard_keys = tuple(req.hard)
    if req.conditional_hard and wants_retirement(snapshot):
        hard_keys = hard_keys + tuple(req.conditional_hard)

    return IntentGaps(
        intent=intent,
        hard_missing=snapshot.missing(hard_keys),
        soft_missing=snapshot.missing(tuple(req.soft)),
    )


def next_field_to_ask(keys: list[str]) -> FieldSpec | None:
    """The one field to ask about, by registry priority."""
    ordered = specs_for(tuple(keys))
    return ordered[0] if ordered else None


# ---------------------------------------------------------------------------
# The /profile/completeness view
# ---------------------------------------------------------------------------


def build_completeness(snapshot: ProfileSnapshot) -> dict[str, Any]:
    """Per-field and per-section status plus which capabilities are blocked."""
    fields: list[dict[str, Any]] = []
    for fs in sorted(FIELD_REGISTRY.values(), key=lambda f: (f.section, f.priority)):
        fields.append(
            {
                "key": fs.key,
                "section": fs.section,
                "label": fs.question,
                "input_kind": fs.input_kind,
                "unit": fs.unit,
                "options": list(fs.options),
                "filled": snapshot.has(fs.key),
            }
        )

    sections: list[dict[str, Any]] = []
    for section, label in _SECTION_LABELS.items():
        in_section = [f for f in fields if f["section"] == section]
        filled = sum(1 for f in in_section if f["filled"])
        sections.append(
            {
                "section": section,
                "label": label,
                "filled": filled,
                "total": len(in_section),
                "complete": len(in_section) > 0 and filled == len(in_section),
            }
        )

    capabilities: list[dict[str, Any]] = []
    for intent in (
        "financial_planning_projection",
        "asset_allocation",
        "rebalancing",
        "additional_investment",
    ):
        g = gaps_for_intent(intent, snapshot)
        capabilities.append(
            {
                "capability": intent,
                "blocked": g.blocked,
                "missing": g.hard_missing,
                "improves_with": g.soft_missing,
            }
        )

    total = len(fields)
    filled_total = sum(1 for f in fields if f["filled"])
    return {
        "filled": filled_total,
        "total": total,
        "percent": round(100 * filled_total / total) if total else 0,
        "sections": sections,
        "fields": fields,
        "capabilities": capabilities,
    }


async def completeness_for_user(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    return build_completeness(await load_snapshot(db, user_id))


__all__ = [
    "IntentGaps",
    "ProfileSnapshot",
    "build_completeness",
    "completeness_for_user",
    "gaps_for_intent",
    "load_snapshot",
    "next_field_to_ask",
    "wants_retirement",
]
