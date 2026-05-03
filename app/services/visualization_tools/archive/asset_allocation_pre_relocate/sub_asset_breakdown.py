"""Chart tool — sub-asset breakdown treemap.

Groups the user's holdings by (parent asset class, sub-class) for a treemap.
Tries to read sub-class from Fund.category first; falls back to a keyword
heuristic on instrument_name when Fund data is missing or unjoinable.
Returns None when no portfolio or no holdings exist.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fund import Fund
from app.models.portfolio import PortfolioHolding
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.schema import (
    SubAssetTreemap,
    TreemapNode,
)

# (keyword, parent, sub-class) — first match wins; checked in order.
_NAME_HEURISTICS: list[tuple[str, str, str]] = [
    ("liquid", "Debt", "Liquid"),
    ("overnight", "Debt", "Liquid"),
    ("short term", "Debt", "Short Duration"),
    ("short duration", "Debt", "Short Duration"),
    ("corporate bond", "Debt", "Corporate Bond"),
    ("credit risk", "Debt", "Credit Risk"),
    ("gilt", "Debt", "Government"),
    ("government securit", "Debt", "Government"),
    ("banking & psu", "Debt", "Banking & PSU"),
    ("ultra short", "Debt", "Ultra Short"),
    ("dynamic bond", "Debt", "Dynamic Bond"),
    ("low duration", "Debt", "Low Duration"),
    ("medium duration", "Debt", "Medium Duration"),
    ("long duration", "Debt", "Long Duration"),
    ("large cap", "Equity", "Large Cap"),
    ("largecap", "Equity", "Large Cap"),
    ("mid cap", "Equity", "Mid Cap"),
    ("midcap", "Equity", "Mid Cap"),
    ("small cap", "Equity", "Small Cap"),
    ("smallcap", "Equity", "Small Cap"),
    ("flexi cap", "Equity", "Flexi Cap"),
    ("flexicap", "Equity", "Flexi Cap"),
    ("multi cap", "Equity", "Multi Cap"),
    ("multicap", "Equity", "Multi Cap"),
    ("elss", "Equity", "ELSS"),
    ("tax saver", "Equity", "ELSS"),
    ("focused", "Equity", "Focused"),
    ("dividend yield", "Equity", "Dividend Yield"),
    ("value", "Equity", "Value"),
    ("contra", "Equity", "Contra"),
    ("gold", "Gold", "Gold"),
    ("silver", "Commodities", "Silver"),
    ("hybrid", "Hybrid", "Hybrid"),
    ("balanced", "Hybrid", "Balanced"),
    ("arbitrage", "Hybrid", "Arbitrage"),
    ("equity", "Equity", "Other Equity"),
    ("bond", "Debt", "Other Bond"),
    ("debt", "Debt", "Other Debt"),
]


def _classify(name: str, fund_category: str | None) -> tuple[str, str]:
    """Return (parent_asset_class, sub_class_label) for one holding."""
    if fund_category:
        cat_lower = fund_category.lower()
        for kw, parent, sub in _NAME_HEURISTICS:
            if kw in cat_lower:
                return parent, sub
    name_lower = name.lower()
    for kw, parent, sub in _NAME_HEURISTICS:
        if kw in name_lower:
            return parent, sub
    return "Other", "Unclassified"


async def build_sub_asset_breakdown(
    db: AsyncSession, user_id: uuid.UUID
) -> SubAssetTreemap | None:
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = select(PortfolioHolding).where(
        PortfolioHolding.portfolio_id == portfolio.id
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None

    # Best-effort lookup of Fund.category by exact name.
    names = {r.instrument_name for r in rows}
    fund_cat: dict[str, str | None] = {}
    if names:
        fund_stmt = select(Fund.name, Fund.category).where(Fund.name.in_(names))
        for fname, fcat in (await db.execute(fund_stmt)).all():
            fund_cat[fname] = fcat

    aggregated: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        parent, sub = _classify(r.instrument_name, fund_cat.get(r.instrument_name))
        aggregated[(parent, sub)] += float(r.current_value)

    nodes = [
        TreemapNode(label=sub, parent=parent, value=value)
        for (parent, sub), value in sorted(
            aggregated.items(), key=lambda kv: kv[1], reverse=True
        )
        if value > 0
    ]
    if not nodes:
        return None

    return SubAssetTreemap(
        title="Sub-Asset Breakdown",
        nodes=nodes,
    )
