"""Word → facet lexicon at the chat extraction boundary (S2 spec §3.2).

The Haiku detector classifies WHAT the customer named into a closed
vocabulary of customer-facing targets (``PreferenceTarget``) plus a
five-state level; this module turns that into the S1 wire format the save
flow and the engine speak. Numbers are never chosen here — a ``number``
level carries only a figure the customer actually stated.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

PreferenceTarget = Literal[
    "equity",
    "debt",
    "gold",
    "large_cap",
    "mid_cap",
    "small_cap",
    "value",
    "sector",
    "us_international",
    "multi_asset",
    "short_debt",
    "arbitrage",
    "other",
]
PreferenceLevel = Literal["more", "heavy", "less", "none", "number"]


class PreferenceAsk(BaseModel):
    """One thing the customer wants more/less/none of, in customer vocabulary."""

    target: PreferenceTarget = Field(
        description=(
            "WHAT the customer named. Asset classes: equity, debt, gold. Fund "
            "categories: large_cap / mid_cap / small_cap (any large/mid/small-cap "
            "wording), value, sector (sectoral/thematic), us_international (US, "
            "global, international, overseas funds), multi_asset (multi-asset / "
            "hybrid funds — exclusion only), short_debt (liquid / short-term "
            "debt), arbitrage. "
            "Anything else (a named fund, 'banking funds', ESG, dividend) → other."
        )
    )
    level: PreferenceLevel = Field(
        description=(
            "How much: more (a bit more / increase / add / tilt toward), heavy "
            "(heavy / mostly / lots of / aggressive on), less (a bit less / reduce "
            "/ trim), none (drop / remove / exclude / no X / not interested in), "
            "number (ONLY when the customer states a percentage: '100% equity', "
            "'make gold 30%', 'take equity to 70%' — put it in number)."
        )
    )
    number: Optional[float] = Field(
        default=None,
        description="The percentage the customer STATED, 0-100. Only with level=number. Never invent one.",
    )
    other_words: Optional[str] = Field(
        default=None,
        description="With target=other: the customer's words verbatim (e.g. 'banking funds').",
    )


TARGET_TO_FACET: dict[str, tuple[str, str]] = {
    "equity": ("asset_class", "equity"),
    "debt": ("asset_class", "debt"),
    "gold": ("subgroup", "gold_commodities"),
    "large_cap": ("subgroup", "low_beta_equities"),
    "mid_cap": ("subgroup", "medium_beta_equities"),
    "small_cap": ("subgroup", "high_beta_equities"),
    "value": ("subgroup", "value_equities"),
    "sector": ("subgroup", "sector_equities"),
    "us_international": ("subgroup", "us_equities"),
    "multi_asset": ("subgroup", "multi_asset"),
    "short_debt": ("subgroup", "short_debt"),
    "arbitrage": ("subgroup", "arbitrage"),
}


def build_intent(asks: Optional[list[PreferenceAsk]]) -> tuple[dict[str, Any], list[str]]:
    """S1 wire-format intent from extracted asks, plus the words of every ask
    that could not be mapped (unknown target, or a number level with no usable
    figure). The wire format carries ONE asset_class facet, so the first
    class-level ask wins."""
    intent: dict[str, Any] = {}
    subgroups: dict[str, Any] = {}
    unmapped: list[str] = []
    for ask in asks or []:
        facet = TARGET_TO_FACET.get(ask.target)
        words = (ask.other_words or ask.target.replace("_", " ")).strip()
        if facet is None:
            unmapped.append(words)
            continue
        value: Any = ask.level
        if ask.level == "number":
            if ask.number is None or not 0 <= ask.number <= 100:
                unmapped.append(words)
                continue
            value = float(ask.number)
        kind, key = facet
        if key == "multi_asset" and value != "none":
            unmapped.append(words)
            continue
        if kind == "asset_class":
            if "asset_class" in intent:
                continue
            if ask.level == "number":
                intent["asset_class"] = {"class": key, "direction": "target", "target_pct": value}
            else:
                intent["asset_class"] = {"class": key, "direction": ask.level}
        else:
            subgroups[key] = value
    if subgroups:
        intent["subgroups"] = subgroups
    return intent, unmapped
