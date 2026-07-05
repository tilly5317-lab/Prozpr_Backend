"""Current-holdings snapshot aggregated to canonical asset subgroups.

Feeds the deficit-fill lumpsum path (spec 2026-07-03): the per-subgroup values
are the `current` side of ``deficit = ideal - current``, and the frozen values
(held ELSS, direct stocks) pin PAA's ``elss_corpus`` / ``non_mf_equity_corpus``
so locked money is not spread over buyable subgroups. Valuation source is the
precomputed ``PortfolioHolding.current_value`` (product decision 2026-07-03:
lighter than the transaction ledger; both sides of the deficit share this one
snapshot, so staleness cancels). Classification goes through the canonical
``classify_holding`` — the same vocabulary as PAA's subgroup rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.mutual_funds.services.scheme_classification import classify_holding
from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding

# allocation_rollup convention: these instrument types are direct equity.
_EQUITY_INSTRUMENT_TYPES = frozenset({"equity", "stock", "share"})

_SUBGROUP_NON_MF_EQUITIES = "non_mf_equities"
_SUBGROUP_ELSS = "tax_efficient_equities"


@dataclass(frozen=True)
class HoldingsSnapshot:
    """Classified current-value totals. ``by_subgroup`` uses the canonical
    scheme_classification vocabulary (frozen subgroups included); unclassifiable
    value is carried only in ``unknown_inr`` (in the total, no gap row)."""

    by_subgroup: dict[str, float] = field(default_factory=dict)
    unknown_inr: float = 0.0

    @property
    def total_inr(self) -> float:
        return sum(self.by_subgroup.values()) + self.unknown_inr

    @property
    def elss_inr(self) -> float:
        return self.by_subgroup.get(_SUBGROUP_ELSS, 0.0)

    @property
    def non_mf_equity_inr(self) -> float:
        return self.by_subgroup.get(_SUBGROUP_NON_MF_EQUITIES, 0.0)


def aggregate_holdings(
    rows: list[tuple[str | None, float, str | None, str | None]],
) -> HoldingsSnapshot:
    """Pure aggregation over ``(instrument_type, current_value, sub_category,
    scheme_name)`` tuples. Direct-stock rows (instrument_type in the equity set)
    bucket to non_mf_equities WITHOUT classification; everything else classifies
    via ``classify_holding``; ``(None, None)`` results accrue to unknown_inr."""
    by_subgroup: dict[str, float] = {}
    unknown = 0.0
    for instrument_type, current_value, sub_category, scheme_name in rows:
        value = float(current_value or 0.0)
        if value <= 0:
            continue
        if (instrument_type or "").strip().lower() in _EQUITY_INSTRUMENT_TYPES:
            key: str | None = _SUBGROUP_NON_MF_EQUITIES
        else:
            _asset_class, key = classify_holding(sub_category, scheme_name)
        if key is None:
            unknown += value
            continue
        by_subgroup[key] = by_subgroup.get(key, 0.0) + value
    return HoldingsSnapshot(by_subgroup=by_subgroup, unknown_inr=unknown)


async def load_holdings_snapshot(
    db: AsyncSession, user_id: uuid.UUID
) -> HoldingsSnapshot:
    """Load + classify the user's holdings across their portfolios.

    ``fund_metadata`` joins on scheme_code (see the PortfolioHolding
    relationship); rows without metadata fall back to the instrument name so
    name-based classification overrides still get a chance."""
    stmt = (
        select(PortfolioHolding)
        .join(Portfolio, PortfolioHolding.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user_id)
        .options(selectinload(PortfolioHolding.fund_metadata))
    )
    holdings = (await db.execute(stmt)).scalars().all()
    rows = [
        (
            h.instrument_type,
            float(h.current_value or 0.0),
            h.fund_metadata.sub_category if h.fund_metadata else None,
            h.fund_metadata.scheme_name if h.fund_metadata else h.instrument_name,
        )
        for h in holdings
    ]
    return aggregate_holdings(rows)
