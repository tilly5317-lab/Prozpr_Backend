"""Central registry of chart tools.

Each registered tool carries:
- ``name``        — stable identifier matching the payload's ``type`` discriminator
- ``description`` — natural-language sentence read by the selector LLM to decide relevance
- ``builder``     — async callable producing a typed ChartPayload (or None if data missing)
- ``payload_cls`` — the Pydantic v2 payload class the builder returns; used by the
                    auto-doc generator and (later) by frontend type-export tooling.
                    Carried explicitly because Python 3.9's runtime ``X | None``
                    syntax can't be evaluated by ``typing.get_type_hints`` even
                    with ``from __future__ import annotations``.

Adding a new chart = create the chart's per-folder package and register one entry below.
The selector and dispatcher in ``brain.py`` read from this dict; nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel

from app.services.visualization_tools.buy_sell_ledger.builder import (
    build_buy_sell_ledger,
)
from app.services.visualization_tools.buy_sell_ledger.schema import BuySellLedger
from app.services.visualization_tools.category_gap_bar.builder import (
    build_category_gap_bar,
)
from app.services.visualization_tools.category_gap_bar.schema import CategoryGapBar
from app.services.visualization_tools.concentration_risk.builder import (
    build_concentration_risk,
)
from app.services.visualization_tools.concentration_risk.schema import ConcentrationRisk
from app.services.visualization_tools.current_donut.builder import build_current_donut
from app.services.visualization_tools.current_donut.schema import CurrentDonut
from app.services.visualization_tools.planned_donut.builder import build_planned_donut
from app.services.visualization_tools.planned_donut.schema import PlannedDonut
from app.services.visualization_tools.profile_dial.builder import build_profile_dial
from app.services.visualization_tools.profile_dial.schema import ProfileDial
from app.services.visualization_tools.target_vs_actual.builder import (
    build_target_vs_actual,
)
from app.services.visualization_tools.target_vs_actual.schema import TargetVsActual
from app.services.visualization_tools.tax_cost_bar.builder import build_tax_cost_bar
from app.services.visualization_tools.tax_cost_bar.schema import TaxCostBar
from app.services.visualization_tools.top_bottom_funds.builder import (
    build_top_bottom_funds,
)
from app.services.visualization_tools.top_bottom_funds.schema import TopBottomFunds


@dataclass(frozen=True)
class ChartTool:
    name: str
    description: str
    builder: Callable[..., Any]
    payload_cls: Type[BaseModel]


CHART_TOOLS: dict[str, ChartTool] = {
    "buy_sell_ledger": ChartTool(
        name="buy_sell_ledger",
        description=(
            "Per-fund table of buy and sell amounts (₹) from a rebalancing trade "
            "plan, sorted by absolute trade size. Use when the user asks 'what trades "
            "should I do', 'just show me the trades', the actual buys and sells, or "
            "wants the executable steps from a rebalance."
        ),
        builder=build_buy_sell_ledger,
        payload_cls=BuySellLedger,
    ),
    "category_gap_bar": ChartTool(
        name="category_gap_bar",
        description=(
            "Grouped horizontal bar chart showing Current / Target / Plan allocation "
            "(in ₹) per SEBI sub-category. Use when the user asks about gaps, drift, "
            "'how off am I', 'what should I be holding', or generic 'rebalance my "
            "portfolio' with no specific framing — this is the default chart for "
            "rebalancing questions."
        ),
        builder=build_category_gap_bar,
        payload_cls=CategoryGapBar,
    ),
    "current_donut": ChartTool(
        name="current_donut",
        description=(
            "Donut chart of the user's current asset allocation, broken down by class "
            "(equity, debt, gold, liquid, cash, etc.) with the total portfolio value at "
            "the centre. Use whenever the user asks about their current portfolio "
            "composition, asset mix, allocation breakdown, holdings split, or wants to "
            "see what they own at a glance — including follow-ups like 'show me again' "
            "or 'what's my mix'."
        ),
        builder=build_current_donut,
        payload_cls=CurrentDonut,
    ),
    "planned_donut": ChartTool(
        name="planned_donut",
        description=(
            "Donut chart of the post-rebalance allocation share by SEBI sub-category. "
            "Use when the user asks about the resulting/final portfolio shape, 'what "
            "will it look like after I rebalance', or proportions of the planned mix."
        ),
        builder=build_planned_donut,
        payload_cls=PlannedDonut,
    ),
    "profile_dial": ChartTool(
        name="profile_dial",
        description=(
            "Gauge / dial showing the user's risk profile from Conservative to "
            "Aggressive (5 bands). Use when the user asks about their risk profile, "
            "risk score, 'how aggressive is my profile', risk capacity, or how their "
            "profile compares to the spectrum."
        ),
        builder=build_profile_dial,
        payload_cls=ProfileDial,
    ),
    "concentration_risk": ChartTool(
        name="concentration_risk",
        description=(
            "Horizontal bar chart of the user's top-5 holdings by value plus a 'rest' "
            "bar, with a severity badge (diversified / watch / concentrated). Use when "
            "the user asks about concentration, diversification, biggest holdings, "
            "single-fund risk, 'how spread out is my portfolio', or whether they're "
            "over-exposed to any one fund."
        ),
        builder=build_concentration_risk,
        payload_cls=ConcentrationRisk,
    ),
    "target_vs_actual": ChartTool(
        name="target_vs_actual",
        description=(
            "Paired bar chart comparing the user's target (ideal/recommended) "
            "allocation against their actual current allocation, per asset class, with "
            "drift labels. Use when the user asks about whether they're on-track vs "
            "their plan, drift from target, rebalancing needs, gap to ideal, or how "
            "their actual mix compares to what was recommended."
        ),
        builder=build_target_vs_actual,
        payload_cls=TargetVsActual,
    ),
    "tax_cost_bar": ChartTool(
        name="tax_cost_bar",
        description=(
            "Stacked horizontal bar chart of realised short-term + long-term gains "
            "and exit loads per SEBI sub-category, plus headline totals. Use when "
            "the user asks about cost, taxes, exit loads, 'is rebalancing worth it', "
            "or trade-offs of the rebalance."
        ),
        builder=build_tax_cost_bar,
        payload_cls=TaxCostBar,
    ),
    "top_bottom_funds": ChartTool(
        name="top_bottom_funds",
        description=(
            "Bar chart of the top-3 and bottom-3 funds in the user's portfolio by "
            "1-year return, with a portfolio-average reference line. Use when the "
            "user asks about which funds are performing well or poorly, 'best and "
            "worst', 'which funds are dragging', or fund-level performance comparison."
        ),
        builder=build_top_bottom_funds,
        payload_cls=TopBottomFunds,
    ),
}
