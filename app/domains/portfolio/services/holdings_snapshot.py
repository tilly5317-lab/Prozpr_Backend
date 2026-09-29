"""Current-holdings snapshot aggregated to canonical asset subgroups.

Pure: classifies already-loaded holdings through the canonical
``classify_holding`` (the same vocabulary as the practical allocation's subgroup
rows), valued at ``PortfolioHolding.current_value``. Lives here, not in
additional_investment, because the practical-allocation builder — which
rebalancing imports — reads it too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domains.mutual_funds.services.scheme_classification import classify_holding

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


def snapshot_from_holdings(holdings: Iterable[Any]) -> HoldingsSnapshot:
    """Classify already-loaded PortfolioHolding rows (fund metadata preloaded)."""
    return aggregate_holdings(
        [
            (
                h.instrument_type,
                float(h.current_value or 0.0),
                h.fund_metadata.sub_category if h.fund_metadata else None,
                h.fund_metadata.scheme_name if h.fund_metadata else h.instrument_name,
            )
            for h in holdings
        ]
    )
