"""group_flows: pre-computed buy/sell subtotals per customer-facing group, so the
formatter never sums bucket amounts in prose (that arithmetic hallucinated crore
figures). Aggregated from buckets; friendly labels only, never a raw subgroup name."""
from types import SimpleNamespace

from app.domains.rebalancing.services.rebal_engine.service import build_rebal_facts_pack


def _action(sub_category, fund, *, current=0.0, buy=0.0, sell=0.0):
    return SimpleNamespace(
        present_allocation_inr=current, pass1_buy_amount=buy,
        pass1_sell_amount=sell, pass2_sell_amount=0.0,
        sub_category=sub_category, recommended_fund=fund,
        selection_reason="", rejection_reason="",
    )


def _subgroup(asset_subgroup, actions):
    return SimpleNamespace(
        asset_subgroup=asset_subgroup, actions=actions,
        current_holding_inr=sum(a.present_allocation_inr for a in actions),
    )


def _pack(subgroups):
    return build_rebal_facts_pack(SimpleNamespace(subgroups=subgroups))


def test_multi_bucket_group_total_is_precomputed():
    # The regression: three sub_categories under one subgroup must be pre-summed,
    # so the LLM copies "₹150" instead of adding 100+50 in prose.
    pack = _pack([
        _subgroup("multi_asset", [
            _action("Aggressive Hybrid Fund", "AH", buy=1_000_000.0),
            _action("Flexi Cap Fund", "FC", buy=500_000.0),
        ]),
        _subgroup("us_equities", [_action("FoF Overseas", "US", buy=200_000.0)]),
    ])
    gf = {g["group"]: g for g in pack["group_flows"]}
    assert gf["Multi-asset & hybrid funds"]["buy_indian"] == "₹15 lakh"
    assert gf["US & international equity"]["buy_indian"] == "₹2 lakh"
    assert "buy_inr" not in gf["Multi-asset & hybrid funds"]  # _inr dropped from output


def test_group_row_carries_held_total_sorted_by_exposure_no_raw_leak():
    pack = _pack([
        _subgroup("high_beta_equities", [_action("Small Cap Fund", "SC", current=300.0, sell=200.0)]),
        _subgroup("multi_asset", [_action("Aggressive Hybrid Fund", "AH", buy=50.0)]),
        _subgroup("low_beta_equities", [_action("Large Cap Fund", "LC", current=100.0)]),  # held-as-is
    ])
    gf = {g["group"]: g for g in pack["group_flows"]}
    labels = [g["group"] for g in pack["group_flows"]]
    # It is the customer-facing TABLE, so held-as-is groups appear too; sorted by
    # exposure (max current/planned) desc: small-cap 300 > large-cap 100 > multi 50.
    assert labels == ["Small-cap equity", "Large-cap equity", "Multi-asset & hybrid funds"]
    assert all("_" not in g["group"] for g in pack["group_flows"])  # no internal name leaks
    # the group carries its OWN held total -> "sell 200 out of 300 held" pairs right
    assert gf["Small-cap equity"]["current_indian"] == "₹300"
    assert gf["Small-cap equity"]["sell_indian"] == "₹200"
    assert gf["Small-cap equity"]["planned_final_indian"] == "₹100"
    assert gf["Small-cap equity"]["net_change_indian"] == "−₹200"  # signed, pre-computed
    assert gf["Multi-asset & hybrid funds"]["net_change_indian"] == "+₹50"  # buy 50, sell 0
    assert "current_inr" not in gf["Small-cap equity"]  # _inr stripped from output
    assert "net_change_inr" not in gf["Small-cap equity"]


def test_unknown_subgroup_falls_back_to_other_funds():
    pack = _pack([_subgroup("brand_new_subgroup", [_action("Weird Fund", "W", buy=10.0)])])
    assert pack["group_flows"][0]["group"] == "Other funds"
