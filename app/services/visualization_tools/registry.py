"""Central registry of chart tools.

Each registered tool carries:
- `name`     — stable identifier matching the payload's `type` discriminator
- `description` — natural-language sentence read by the selector LLM to decide relevance
- `builder`  — async callable `(db, user_id, **kwargs) -> ChartPayload | None`

Adding a new chart = one entry below. The selector and dispatch in `brain.py`
read from this dict; nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.visualization_tools.asset_allocation.concentration_risk import (
    build_concentration_risk,
)
from app.services.visualization_tools.asset_allocation.current_allocation import (
    build_current_allocation_donut,
)
from app.services.visualization_tools.asset_allocation.sub_asset_breakdown import (
    build_sub_asset_breakdown,
)
from app.services.visualization_tools.asset_allocation.target_vs_actual import (
    build_target_vs_actual,
)


@dataclass(frozen=True)
class ChartTool:
    name: str
    description: str
    builder: Callable[..., Any]


CHART_TOOLS: dict[str, ChartTool] = {
    "allocation.current_donut": ChartTool(
        name="allocation.current_donut",
        description=(
            "Donut chart of the user's current asset allocation, broken down by class "
            "(equity, debt, gold, liquid, cash, etc.) with the total portfolio value at "
            "the centre. Use whenever the user asks about their current portfolio "
            "composition, asset mix, allocation breakdown, holdings split, or wants to "
            "see what they own at a glance — including follow-ups like 'show me again' "
            "or 'what's my mix'."
        ),
        builder=build_current_allocation_donut,
    ),
    "allocation.concentration_risk": ChartTool(
        name="allocation.concentration_risk",
        description=(
            "Horizontal bar chart of the user's top-5 holdings by value plus a 'rest' "
            "bar, with a severity badge (diversified / watch / concentrated). Use when "
            "the user asks about concentration, diversification, biggest holdings, "
            "single-fund risk, 'how spread out is my portfolio', or whether they're "
            "over-exposed to any one fund."
        ),
        builder=build_concentration_risk,
    ),
    "allocation.sub_asset_treemap": ChartTool(
        name="allocation.sub_asset_treemap",
        description=(
            "Treemap of holdings grouped by sub-asset class within each parent asset "
            "class — e.g. inside Equity: large cap / mid cap / small cap / flexi cap; "
            "inside Debt: liquid / short duration / corporate bond. Use when the user "
            "asks about sub-class breakdown, equity style mix (large/mid/small cap "
            "split), debt duration mix, or wants more granular detail than the donut."
        ),
        builder=build_sub_asset_breakdown,
    ),
    "allocation.target_vs_actual": ChartTool(
        name="allocation.target_vs_actual",
        description=(
            "Paired bar chart comparing the user's target (ideal/recommended) "
            "allocation against their actual current allocation, per asset class, with "
            "drift labels. Use when the user asks about whether they're on-track vs "
            "their plan, drift from target, rebalancing needs, gap to ideal, or how "
            "their actual mix compares to what was recommended."
        ),
        builder=build_target_vs_actual,
    ),
}
