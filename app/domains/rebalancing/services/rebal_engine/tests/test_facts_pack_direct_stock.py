"""The facts pack must explain where the buy money comes from.

Step4 now funds mutual-fund buys with the direct-stock proceeds the NFA band
frees up (`excess_direct_stocks_inr`, surfaced as a single SELL_DIRECT_STOCKS
action). Those rupees are deliberately NOT part of `total_sell_inr`, which sums
over fund rows only.

So on any customer holding direct equity, `buys_total` legitimately exceeds
`sells_total` — Neha's plan buys ₹16.5L against ₹4.6L of fund sales. Without the
stock sale in the pack the LLM has an ₹11.9L hole it cannot account for, and the
most likely question a customer asks about a rebalance is exactly "where is the
money coming from?".
"""

from __future__ import annotations

from app.domains.rebalancing.services.rebal_engine.service import (
    build_rebal_facts_pack,
)


def test_direct_stock_sale_is_in_the_pack(fixture_rebalancing_response, monkeypatch):
    cb = fixture_rebalancing_response.practical_allocation.corpus_breakdown
    monkeypatch.setattr(cb, "excess_direct_stocks_inr", 225000, raising=False)

    pack = build_rebal_facts_pack(fixture_rebalancing_response)

    assert pack["direct_stock_sale_inr"] == 225000.0
    assert "lakh" in pack["direct_stock_sale_indian"]


def test_buys_are_accounted_for_by_sells_plus_stock_proceeds(
    fixture_rebalancing_response, monkeypatch
):
    """The pack must not present an unexplained gap."""
    cb = fixture_rebalancing_response.practical_allocation.corpus_breakdown
    monkeypatch.setattr(cb, "excess_direct_stocks_inr", 225000, raising=False)

    pack = build_rebal_facts_pack(fixture_rebalancing_response)

    funded_by = pack["sells_total_inr"] + pack["direct_stock_sale_inr"]
    assert pack["buys_total_inr"] <= funded_by + 1  # ₹1 rounding tolerance


def test_field_omitted_when_no_direct_stock_is_sold(fixture_rebalancing_response, monkeypatch):
    """Nothing to explain, so nothing to surface — keeps the pack lean."""
    cb = fixture_rebalancing_response.practical_allocation.corpus_breakdown
    monkeypatch.setattr(cb, "excess_direct_stocks_inr", 0, raising=False)

    pack = build_rebal_facts_pack(fixture_rebalancing_response)

    assert "direct_stock_sale_inr" not in pack
