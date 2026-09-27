"""Tests for the shared rebalancing asset-class rollup (Invest page + chat).

``asset_class_mix_from_rows`` is THE Equity/Debt/Others rollup for a run. Both
the Invest-page bars and the chat facts pack call it, so the two surfaces cannot
quote different splits for one run — the parity test at the bottom is the
regression that pins that.

CURRENT and TARGET both look every row through on its own sub_category: they
describe concrete funds, so the same untraded rupee has to land in the same asset
class on both bars. The 65/25/10 sleeve split applies only where there is no fund
identity at all — subgroup totals (the GOAL mix, the legacy fallback).
"""

import pytest

from app.domains.rebalancing.services.asset_class_breakdown import (
    current_mix_from_rows,
    goal_asset_class_mix,
    plan_rows_from_run,
    target_asset_class_mix,
    target_mix_from_rows,
)
from app.domains.rebalancing.services.rebal_engine.service import (
    _asset_class_mix_from_buckets,
)


def _sg(asset_subgroup, suggested_final, goal_target=0.0):
    return type(
        "SG",
        (),
        {
            "asset_subgroup": asset_subgroup,
            "suggested_final_holding_inr": suggested_final,
            "goal_target_inr": goal_target,
        },
    )()


def _fund_row(asset_subgroup, sub_category, isin, present):
    return type(
        "FR",
        (),
        {
            "asset_subgroup": asset_subgroup,
            "sub_category": sub_category,
            "isin": isin,
            "present_allocation_inr": present,
        },
    )()


def _trade(asset_subgroup, sub_category, isin, action, amount):
    return type(
        "T",
        (),
        {
            "asset_subgroup": asset_subgroup,
            "sub_category": sub_category,
            "isin": isin,
            "action": action,
            "amount_inr": amount,
        },
    )()


# --------------------------------------------------------------------------
# The shared rollup
# --------------------------------------------------------------------------


def test_pure_categories_map_to_their_subgroup_class():
    rows = [
        ("low_beta_equities", "Large Cap Fund", 100.0),
        ("near_debt", "Liquid Fund", 50.0),
        ("gold_commodities", "Gold ETF", 25.0),
    ]
    assert current_mix_from_rows(rows) == pytest.approx(
        {"Equity": 100.0, "Debt": 50.0, "Others": 25.0}
    )


def test_genuine_hybrid_is_looked_through():
    rows = [("medium_beta_equities", "Aggressive Hybrid Fund", 100.0)]
    assert current_mix_from_rows(rows) == pytest.approx(
        {"Equity": 72.5, "Debt": 17.5, "Others": 10.0}
    )


def test_flexi_cap_is_not_looked_through():
    # Pure-equity SEBI category — must not manufacture debt the customer
    # does not own. This is what put chat at 95/3/2 while the page said 98/1/0.
    rows = [("medium_beta_equities", "Flexi Cap Fund", 100.0)]
    assert current_mix_from_rows(rows) == pytest.approx({"Equity": 100.0})


def test_missing_sub_category_falls_back_to_the_subgroup():
    rows = [("near_debt", None, 100.0)]
    assert current_mix_from_rows(rows) == pytest.approx({"Debt": 100.0})


def test_target_falls_back_to_the_sleeve_only_without_a_sub_category():
    # A fund-less multi_asset row is sleeve headroom, so it splits 65/25/10.
    rows = [("low_beta_equities", "Large Cap Fund", 100.0), ("multi_asset", None, 100.0)]
    assert target_mix_from_rows(rows) == pytest.approx(
        {"Equity": 165.0, "Debt": 25.0, "Others": 10.0}
    )


def test_target_reports_a_named_fund_in_the_sleeve_as_that_fund():
    # THE 2026-09-27 fix. A Flexi Cap fund does land in the multi_asset subgroup.
    # The target bar used to force it to 65/25/10 while the current bar looked it
    # through, so a customer whose plan could not sell it saw ~250 of Debt and 100
    # of Others appear out of money that never moved — a bar matching neither their
    # goal mix nor what they would hold.
    rows = [("multi_asset", "Flexi Cap Fund", 1000.0)]
    assert target_mix_from_rows(rows) == pytest.approx({"Equity": 1000.0})


def test_current_and_target_agree_on_an_untraded_sleeve_holding():
    # The invariant behind that fix: no trade, no change of asset class.
    rows = [
        ("multi_asset", "Flexi Cap Fund", 1000.0),
        ("multi_asset", "Aggressive Hybrid Fund", 400.0),
    ]
    assert current_mix_from_rows(rows) == pytest.approx(target_mix_from_rows(rows))


def test_current_does_not_split_the_sleeve():
    # The sleeve composition describes an allocation the plan has not filled;
    # a fund already sitting there is just that fund.
    rows = [("multi_asset", "Flexi Cap Fund", 1000.0)]
    assert current_mix_from_rows(rows) == pytest.approx({"Equity": 1000.0})


def test_rollup_conserves_the_total():
    rows = [
        ("medium_beta_equities", "Aggressive Hybrid Fund", 300.0),
        ("multi_asset", None, 200.0),
        ("near_debt", "Liquid Fund", 500.0),
    ]
    for mix in (current_mix_from_rows(rows), target_mix_from_rows(rows)):
        assert sum(mix.values()) == pytest.approx(1000.0)


# --------------------------------------------------------------------------
# Row construction from a persisted run
# --------------------------------------------------------------------------


def test_plan_rows_apply_buys_and_sells():
    fund_rows = [
        _fund_row("low_beta_equities", "Large Cap Fund", "INF001", 100.0),
        _fund_row("near_debt", "Liquid Fund", "INF002", 0.0),
    ]
    trades = [
        _trade("low_beta_equities", "Large Cap Fund", "INF001", "sell", 40.0),
        _trade("near_debt", "Liquid Fund", "INF002", "buy", 40.0),
    ]
    current_rows, target_rows = plan_rows_from_run(fund_rows, trades)

    assert current_mix_from_rows(current_rows) == pytest.approx(
        {"Equity": 100.0, "Debt": 0.0}
    )
    assert target_mix_from_rows(target_rows) == pytest.approx(
        {"Equity": 60.0, "Debt": 40.0}
    )
    # A rebalance moves money; it does not create or destroy it.
    assert sum(a for _sgp, _sc, a in target_rows) == pytest.approx(100.0)


def test_plan_rows_handle_a_negative_signed_sell_amount():
    fund_rows = [_fund_row("low_beta_equities", "Large Cap Fund", "INF001", 100.0)]
    trades = [_trade("low_beta_equities", "Large Cap Fund", "INF001", "sell", -40.0)]
    _current, target_rows = plan_rows_from_run(fund_rows, trades)
    assert sum(a for _sgp, _sc, a in target_rows) == pytest.approx(60.0)


# --------------------------------------------------------------------------
# Legacy subgroup fallback (runs persisted without fund rows)
# --------------------------------------------------------------------------


def test_legacy_subgroup_target_still_splits_the_sleeve():
    mix = target_asset_class_mix(
        [_sg("low_beta_equities", 100.0), _sg("short_debt", 50.0), _sg("multi_asset", 100.0)]
    )
    assert mix == pytest.approx({"Equity": 165.0, "Debt": 75.0, "Others": 10.0})
    assert sum(mix.values()) == pytest.approx(250.0)


def test_legacy_subgroup_target_surfaces_others_without_a_gold_subgroup():
    mix = target_asset_class_mix([_sg("medium_beta_equities", 0.0), _sg("multi_asset", 1000.0)])
    assert mix["Others"] == pytest.approx(100.0)


# --------------------------------------------------------------------------
# The GOAL mix — what the plan AIMED at, read off goal_target_inr
# --------------------------------------------------------------------------


def test_goal_mix_reads_goal_target_and_keeps_the_sleeve_split():
    # goal_target_inr is subgroup headroom with no fund attached, so the sleeve
    # composition is the only thing that can describe it.
    mix = goal_asset_class_mix(
        [
            _sg("low_beta_equities", 0.0, goal_target=100.0),
            _sg("short_debt", 0.0, goal_target=100.0),
            _sg("multi_asset", 0.0, goal_target=100.0),
        ]
    )
    assert mix == pytest.approx({"Equity": 165.0, "Debt": 125.0, "Others": 10.0})


def test_goal_mix_is_independent_of_where_the_plan_lands():
    # The whole point of shipping it: a plan blocked from trading reads goal far
    # from target. Here the goal wants no equity at all and the plan holds only
    # equity.
    subs = [
        _sg("low_beta_equities", 500.0, goal_target=0.0),
        _sg("short_debt", 0.0, goal_target=500.0),
    ]
    assert goal_asset_class_mix(subs) == pytest.approx({"Equity": 0.0, "Debt": 500.0})
    assert target_asset_class_mix(subs) == pytest.approx({"Equity": 500.0, "Debt": 0.0})


# --------------------------------------------------------------------------
# THE invariant: chat and the Invest page agree on the same run
# --------------------------------------------------------------------------


def test_chat_facts_mix_matches_the_invest_page_for_the_same_run():
    """The regression this whole change exists for.

    Chat reported 95/3/2 as the mix while the Invest page reported 98/1/0 for the
    same holdings, and chat had no target at all — so when the customer asked what
    the plan was moving them toward, the formatter cited the current mix and called
    it the target. Both surfaces now derive from one rollup; this pins them.
    """
    fund_rows = [
        _fund_row("medium_beta_equities", "Flexi Cap Fund", "INF001", 600.0),
        _fund_row("medium_beta_equities", "Aggressive Hybrid Fund", "INF002", 200.0),
        _fund_row("near_debt", "Liquid Fund", "INF003", 200.0),
        # Untraded, and inside the multi_asset subgroup — the row that used to be
        # classified one way on the Current bar and another on the Target bar.
        _fund_row("multi_asset", "Flexi Cap Fund", "INF004", 300.0),
    ]
    trades = [
        _trade("medium_beta_equities", "Flexi Cap Fund", "INF001", "sell", 300.0),
        _trade("near_debt", "Liquid Fund", "INF003", "buy", 300.0),
    ]
    current_rows, target_rows = plan_rows_from_run(fund_rows, trades)
    page_current = current_mix_from_rows(current_rows)
    page_target = target_mix_from_rows(target_rows)

    # The chat facts pack builds its buckets from the same run.
    buckets = [
        {
            "asset_subgroup": "medium_beta_equities",
            "sub_category": "Flexi Cap Fund",
            "current_inr": 600.0,
            "planned_final_inr": 300.0,
        },
        {
            "asset_subgroup": "medium_beta_equities",
            "sub_category": "Aggressive Hybrid Fund",
            "current_inr": 200.0,
            "planned_final_inr": 200.0,
        },
        {
            "asset_subgroup": "near_debt",
            "sub_category": "Liquid Fund",
            "current_inr": 200.0,
            "planned_final_inr": 500.0,
        },
        {
            "asset_subgroup": "multi_asset",
            "sub_category": "Flexi Cap Fund",
            "current_inr": 300.0,
            "planned_final_inr": 300.0,
        },
    ]
    chat_current = _asset_class_mix_from_buckets(
        buckets, amount_key="current_inr", multi_asset_sleeve=False
    )
    chat_target = _asset_class_mix_from_buckets(
        buckets, amount_key="planned_final_inr", multi_asset_sleeve=False
    )

    for asset_class, chat_key in (("Equity", "equity"), ("Debt", "debt"), ("Others", "others")):
        assert chat_current[chat_key] == pytest.approx(page_current.get(asset_class, 0.0))
        assert chat_target[chat_key] == pytest.approx(page_target.get(asset_class, 0.0))

    # And the target genuinely differs from the current — the distinction the
    # formatter now has to work with.
    assert chat_target["debt"] > chat_current["debt"]

    # The untraded multi_asset Flexi Cap holding keeps its asset class across both
    # bars: 300 of equity before, 300 of equity after. Under the old sleeve override
    # the Target bar turned it into 195/75/30 and invented debt out of a fund the
    # plan never touched.
    assert page_current["Others"] == pytest.approx(page_target["Others"])
