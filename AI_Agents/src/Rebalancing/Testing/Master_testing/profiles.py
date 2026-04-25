"""Synthetic-holdings overlay for the 5 canonical Prozpr profiles.

Re-exports the `AllocationInput` PROFILES from `goal_based_allocation_pydantic`
and adds a deterministic synthesizer that builds a dummy current portfolio
per profile. The synthesizer:
  - Drifts each subgroup off its goal-allocation target by a fixed scale
    factor so every profile fires both buy and sell candidates.
  - Includes one held-but-not-recommended ("BAD") fund → forced EXIT path.
  - Normalizes the holdings to total exactly `profile.total_corpus`.
"""

from __future__ import annotations

from decimal import Decimal

from goal_based_allocation_pydantic import AllocationInput, GoalAllocationOutput
from goal_based_allocation_pydantic.Master_testing.profiles import PROFILES


# Per-subgroup-index scale factor. Cycles through under/over so every
# profile gets both buys and sells from the recommended-fund side.
SUBGROUP_SCALE: list[Decimal] = [
    Decimal("0.70"), Decimal("1.30"), Decimal("0.90"), Decimal("1.15"),
    Decimal("0.80"), Decimal("1.20"), Decimal("1.00"), Decimal("0.95"),
    Decimal("0.85"), Decimal("1.10"),
]

# BAD fund injected into every profile at this share of corpus before normalisation.
BAD_FUND_SHARE = Decimal("0.10")

# BAD fund identity. A real off-list scheme: HDFC Large Cap Fund (Direct,
# Growth) — exists in the MF universe (`mf_subgroup_mapped.csv`) so the
# nav_cache returns its real NAV, but is not in `Prozpr_fund_ranking.csv`,
# so the bridge naturally classifies it as held-but-not-recommended.
BAD_ISIN = "INF179K01YV8"
BAD_SUB_CATEGORY = "Large Cap Fund"
BAD_ASSET_SUBGROUP = "low_beta_equities"
BAD_FUND_NAME = "HDFC Large Cap Fund - Growth Option - Direct Plan"
# Off-list funds carry no Prozpr rating (we don't cover them). Use the
# neutral default; the EXIT path is driven entirely by is_recommended=False.
BAD_FUND_RATING = 10


class HoldingRecord:
    """Lightweight container for a synthetic holding. The bridge converts
    these into FundRowInput shape."""

    __slots__ = (
        "isin", "asset_subgroup", "sub_category", "fund_name",
        "present_inr", "fund_rating", "is_recommended",
    )

    def __init__(
        self,
        isin: str,
        asset_subgroup: str,
        sub_category: str,
        fund_name: str,
        present_inr: Decimal,
        fund_rating: int = 8,
        is_recommended: bool = True,
    ) -> None:
        self.isin = isin
        self.asset_subgroup = asset_subgroup
        self.sub_category = sub_category
        self.fund_name = fund_name
        self.present_inr = present_inr
        self.fund_rating = fund_rating
        self.is_recommended = is_recommended


def synth_holdings(
    profile: AllocationInput,
    allocation_output: GoalAllocationOutput,
    rank1_lookup: dict[str, dict],
) -> list[HoldingRecord]:
    """Build a dummy current portfolio that totals to `profile.total_corpus`.

    `rank1_lookup` maps `asset_subgroup → {isin, sub_category, fund_name}`
    and is supplied by the bridge from `Prozpr_fund_ranking.csv`.
    """
    corpus = Decimal(str(profile.total_corpus))
    holdings: list[HoldingRecord] = []
    raw_total = Decimal(0)

    for i, sg_row in enumerate(allocation_output.aggregated_subgroups):
        target = Decimal(str(sg_row.total))
        if target <= 0:
            continue
        rank1 = rank1_lookup.get(sg_row.subgroup)
        if rank1 is None:
            continue
        scale = SUBGROUP_SCALE[i % len(SUBGROUP_SCALE)]
        held = (target * scale).quantize(Decimal("1"))
        holdings.append(
            HoldingRecord(
                isin=rank1["isin"],
                asset_subgroup=sg_row.subgroup,
                sub_category=rank1["sub_category"],
                fund_name=rank1["fund_name"],
                present_inr=held,
            )
        )
        raw_total += held

    bad_held = (corpus * BAD_FUND_SHARE).quantize(Decimal("1"))
    holdings.append(
        HoldingRecord(
            isin=BAD_ISIN,
            asset_subgroup=BAD_ASSET_SUBGROUP,
            sub_category=BAD_SUB_CATEGORY,
            fund_name=BAD_FUND_NAME,
            present_inr=bad_held,
            fund_rating=BAD_FUND_RATING,
            is_recommended=False,
        )
    )
    raw_total += bad_held

    if raw_total > 0:
        scale = corpus / raw_total
        for h in holdings:
            h.present_inr = (h.present_inr * scale).quantize(Decimal("1"))
        delta = corpus - sum((h.present_inr for h in holdings), Decimal(0))
        if delta != 0 and holdings:
            largest = max(holdings, key=lambda h: h.present_inr)
            largest.present_inr += delta

    return holdings


__all__ = ["PROFILES", "HoldingRecord", "synth_holdings"]
