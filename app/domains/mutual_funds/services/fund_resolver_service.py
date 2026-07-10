"""Resolve a free-text fund name to a single scheme.

Two concerns, kept separate (audit P6):
  * ``_match_fund_family`` — fund identity: which fund(s) does the name refer to?
  * ``_pick_direct_growth`` — variant canonicalization: among a fund's schemes,
    pick the Direct-Growth one (the canonical track-record representative).

``resolve_fund`` composes them: a fund the customer HOLDS resolves to their own
held scheme; otherwise we match the family and canonicalize to Direct-Growth,
returning ``Ambiguous`` when the name spans more than one fund and ``None`` when
nothing matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models.enums import MfOptionType, MfPlanType
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata

# Pure noise for identity matching; plan/option words are handled structurally.
_STOP = {"fund", "plan", "option", "the", "scheme"}
# Plan/option words removed when reducing a scheme name to its fund identity.
_PLAN_OPTION = {"direct", "regular", "growth", "idcw"}


@dataclass(frozen=True)
class ResolvedFund:
    scheme_code: str
    scheme_name: str
    isin: str | None
    is_held: bool = False


@dataclass(frozen=True)
class Ambiguous:
    candidates: list[str]


def _tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _STOP}


def _fund_key(name: str) -> frozenset[str]:
    """A scheme name reduced to its fund identity (plan/option words dropped)."""
    return frozenset(_tokens(name) - _PLAN_OPTION)


async def _match_fund_family(db: AsyncSession, name: str) -> list[MfFundMetadata]:
    """Active schemes whose name contains every token of the query name."""
    q = _tokens(name)
    if not q:
        return []
    rows = (
        await db.execute(select(MfFundMetadata).where(MfFundMetadata.is_active.is_(True)))
    ).scalars().all()
    return [r for r in rows if q <= _tokens(r.scheme_name)]


def _pick_direct_growth(candidates: list[MfFundMetadata]) -> ResolvedFund | Ambiguous | None:
    """Canonicalize matched schemes to one Direct-Growth fund.

    Prefer Direct-Growth; fall back to any Growth plan when no Direct exists.
    Collapse variant rows of the same fund; more than one distinct fund → Ambiguous.
    """
    pool = [
        c for c in candidates
        if c.plan_type == MfPlanType.DIRECT and c.option_type == MfOptionType.GROWTH
    ]
    if not pool:
        pool = [c for c in candidates if c.option_type == MfOptionType.GROWTH]
    if not pool:
        return None
    by_fund: dict[frozenset[str], MfFundMetadata] = {}
    for c in pool:
        by_fund.setdefault(_fund_key(c.scheme_name), c)
    picks = list(by_fund.values())
    if len(picks) == 1:
        c = picks[0]
        return ResolvedFund(c.scheme_code, c.scheme_name, c.isin)
    return Ambiguous([c.scheme_name for c in picks][:3])


async def resolve_fund(
    db: AsyncSession, name: str, held: dict[str, str] | None = None
) -> ResolvedFund | Ambiguous | None:
    """Resolve a fund name → the customer's held scheme, a canonical Direct-Growth
    scheme, ``Ambiguous`` (spans multiple funds), or ``None`` (no match)."""
    q = _tokens(name)
    for held_name, code in (held or {}).items():
        if q and q <= _tokens(held_name):
            row = (
                await db.execute(
                    select(MfFundMetadata).where(MfFundMetadata.scheme_code == code)
                )
            ).scalars().first()
            if row is not None:
                return ResolvedFund(row.scheme_code, row.scheme_name, row.isin, is_held=True)
            return ResolvedFund(code, name, None, is_held=True)
    candidates = await _match_fund_family(db, name)
    if not candidates:
        return None
    return _pick_direct_growth(candidates)
