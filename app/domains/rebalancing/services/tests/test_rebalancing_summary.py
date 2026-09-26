"""Unit tests for the plan-aware rebalancing headline builder.

The builder is a pure function over a run's totals + per-asset-class direction, so
these tests use lightweight stand-ins (no DB, no ORM) that expose only the
attributes the builder reads.
"""

from types import SimpleNamespace

from app.domains.rebalancing.services.rebalancing_summary import (
    build_rebalance_summary,
)


def _totals(**kw):
    base = dict(
        total_buy_inr=0.0,
        total_sell_inr=0.0,
        net_cash_flow_inr=0.0,
        total_tax_estimate_inr=0.0,
        funds_to_buy_count=0,
        funds_to_sell_count=0,
        funds_to_exit_count=0,
        funds_held_count=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _sub(asset_class, rebalance_inr, *, buy=0.0, sell=0.0, cur=0.0, final=0.0, ideal=None):
    # `final` = suggested_final_holding_inr (where the plan moves you — this drives
    # the headline). `ideal` = goal_target_inr (the unconstrained goal allocation);
    # defaults to `final` since they match unless constraints bind.
    return SimpleNamespace(
        asset_class=asset_class,
        rebalance_inr=rebalance_inr,
        total_buy_inr=buy,
        total_sell_inr=sell,
        current_holding_inr=cur,
        suggested_final_holding_inr=final,
        goal_target_inr=ideal if ideal is not None else final,
    )


def _trade(reason_code, action="EXIT"):
    return SimpleNamespace(reason_code=reason_code, action=action)


def _breakdown(rows):
    # rows: list of (asset_class, current_inr, target_inr) — the look-through split
    # the Invest bars render.
    return SimpleNamespace(
        rows=[
            SimpleNamespace(asset_class=ac, current_inr=c, target_inr=t)
            for ac, c, t in rows
        ]
    )


def test_returns_none_when_totals_missing():
    assert build_rebalance_summary(None, [], []) is None


def test_on_target_when_no_trades():
    s = build_rebalance_summary(_totals(), [], [])
    assert s.title == "You're on target"
    assert "matches your plan" in s.subtitle
    assert s.reason is None


def test_replacing_two_underperforming_funds_with_tax():
    totals = _totals(
        funds_to_exit_count=2,
        funds_to_sell_count=2,
        total_sell_inr=500_000.0,
        total_tax_estimate_inr=12_400.0,
    )
    trades = [_trade("exit_low_rated"), _trade("exit_low_rated")]
    s = build_rebalance_summary(totals, [_sub("Equity", -500_000.0)], trades)
    assert s.title == "Replacing 2 underperforming funds"
    assert "₹12,400" in s.subtitle
    assert s.reason == "They're dragging on the returns your goals depend on."


def test_replacing_off_list_funds_reason():
    totals = _totals(funds_to_exit_count=1, funds_to_sell_count=1, total_sell_inr=80_000.0)
    s = build_rebalance_summary(totals, [_sub("Equity", -80_000.0)], [_trade("exit_bad_fund")])
    assert s.reason == "They're no longer recommended and could hold your goals back."


def test_replacing_single_fund_is_singular():
    totals = _totals(
        funds_to_exit_count=1, funds_to_sell_count=1, total_sell_inr=100_000.0
    )
    s = build_rebalance_summary(totals, [_sub("Equity", -100_000.0)], [])
    assert s.title == "Replacing 1 underperforming fund"


def test_exits_take_priority_over_buys():
    totals = _totals(
        funds_to_exit_count=1,
        funds_to_buy_count=3,
        total_buy_inr=900_000.0,
        total_sell_inr=100_000.0,
    )
    subs = [_sub("Equity", -100_000.0), _sub("Debt", 900_000.0)]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title.startswith("Replacing")


def test_topping_up_subtitle_quotes_weightage_percentage():
    # Debt sits at 20% but its target is 30% → raise its weightage by 10 points.
    totals = _totals(
        funds_to_buy_count=1,
        funds_to_sell_count=1,
        total_buy_inr=1_000_000.0,
        total_sell_inr=1_000_000.0,
    )
    subs = [
        _sub("Debt", 1_000_000.0, buy=1_000_000.0, cur=2_000_000.0, final=3_000_000.0),
        _sub("Equity", -1_000_000.0, sell=1_000_000.0, cur=8_000_000.0, final=7_000_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title == "Topping up your Debt"
    assert "Raising your debt weightage by 10%" in s.subtitle
    assert "₹" not in s.subtitle  # weightage framing, no rupee amount
    assert (
        s.reason
        == "Your cushion has thinned — leaving your nearer-term goals exposed if markets dip."
    )


def test_trimming_subtitle_quotes_weightage_percentage_and_keeps_tax():
    # Equity at 62% vs a 55% target → reduce weightage by 7 points; tax stays.
    totals = _totals(
        funds_to_sell_count=2,
        funds_to_buy_count=1,
        total_sell_inr=900_000.0,
        total_buy_inr=200_000.0,
        total_tax_estimate_inr=12_400.0,
    )
    subs = [
        _sub("Equity", -700_000.0, sell=900_000.0, buy=200_000.0, cur=6_200_000.0, final=5_500_000.0),
        _sub("Debt", 700_000.0, buy=700_000.0, cur=3_800_000.0, final=4_500_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title == "Trimming your Equity back to target"
    assert "Reducing your equity weightage by 7%" in s.subtitle
    assert "₹12,400" in s.subtitle  # tax estimate still shown
    assert "lakh of equity" not in s.subtitle  # no rupee sell amount
    assert (
        s.reason
        == "That's more market risk than your goals call for — a downturn now would set them back."
    )


def test_weightage_follows_plan_destination_not_ideal_goal():
    # The unconstrained ideal (goal_target) wants MORE equity, but constraints force
    # the plan to SELL equity. The card must follow where the plan actually lands
    # (suggested_final), so it trims by the real swing — never the unreachable ideal.
    totals = _totals(
        funds_to_sell_count=1,
        funds_to_buy_count=1,
        total_sell_inr=2_000_000.0,
        total_buy_inr=2_000_000.0,
    )
    subs = [
        # equity now 60%; plan lands at 40% (sells), though the ideal wanted 90%.
        _sub("Equity", -2_000_000.0, sell=2_000_000.0, cur=6_000_000.0, final=4_000_000.0, ideal=9_000_000.0),
        _sub("Debt", 2_000_000.0, buy=2_000_000.0, cur=4_000_000.0, final=6_000_000.0, ideal=1_000_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title == "Trimming your Equity back to target"
    # 60% → 40% is a 20-point trim (plan); the ideal's 90% must NOT leak in.
    assert "Reducing your equity weightage by 20%" in s.subtitle


def test_trim_falls_back_to_gross_sell_without_weightage_data():
    # When current/target weights aren't available, the subtitle falls back to the
    # GROSS sell (₹6 lakh), never the net reduction (₹4.76 lakh).
    totals = _totals(
        funds_to_sell_count=2,
        funds_to_buy_count=1,
        total_sell_inr=600_000.0,
        total_buy_inr=124_000.0,
        total_tax_estimate_inr=12_400.0,
    )
    subs = [_sub("Equity", -476_000.0, sell=600_000.0, buy=124_000.0)]  # no cur/tgt
    s = build_rebalance_summary(totals, subs, [])
    assert "₹6 lakh" in s.subtitle
    assert "4.76" not in s.subtitle


def test_trimming_omits_tax_clause_when_zero_tax():
    totals = _totals(
        funds_to_sell_count=1,
        funds_to_buy_count=1,
        total_sell_inr=700_000.0,
        total_buy_inr=700_000.0,
        total_tax_estimate_inr=0.0,
    )
    subs = [
        _sub("Equity", -700_000.0, sell=700_000.0, cur=6_200_000.0, final=5_500_000.0),
        _sub("Debt", 700_000.0, buy=700_000.0, cur=3_800_000.0, final=4_500_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert "Reducing your equity weightage by 7%" in s.subtitle
    assert "est." not in s.subtitle
    assert "₹" not in s.subtitle


def test_trimming_others_uses_friendly_phrase():
    totals = _totals(
        funds_to_sell_count=1,
        funds_to_buy_count=1,
        total_sell_inr=1_000_000.0,
        total_buy_inr=1_000_000.0,
    )
    subs = [
        _sub("Others", -1_000_000.0, sell=1_000_000.0, cur=2_000_000.0, final=1_000_000.0),
        _sub("Equity", 1_000_000.0, buy=1_000_000.0, cur=8_000_000.0, final=9_000_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title == "Trimming your other assets back to target"
    assert "Reducing your other assets weightage by 10%" in s.subtitle
    assert (
        s.reason
        == "These have grown past plan, pulling weight from the assets your goals rely on."
    )


def test_two_way_rebalance_leads_with_dominant_trim():
    # Buys and sells are close (so net cash-flow is not lopsided), but the plan
    # is clearly trimming equity the most — lead with that, not a generic header.
    totals = _totals(
        funds_to_buy_count=2,
        funds_to_sell_count=2,
        total_buy_inr=250_000.0,
        total_sell_inr=300_000.0,
        total_tax_estimate_inr=9_000.0,
    )
    subs = [
        _sub("Equity", -300_000.0, sell=300_000.0),
        _sub("Debt", 250_000.0, buy=250_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title == "Trimming your Equity back to target"
    assert "Fine-tuning" not in s.title


def test_two_way_rebalance_leads_with_dominant_topup():
    # Same closeness, but debt is the biggest move → top-up framing.
    totals = _totals(
        funds_to_buy_count=2,
        funds_to_sell_count=1,
        total_buy_inr=300_000.0,
        total_sell_inr=250_000.0,
    )
    subs = [
        _sub("Equity", -250_000.0, sell=250_000.0),
        _sub("Debt", 300_000.0, buy=300_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [])
    assert s.title == "Topping up your Debt"
    assert "₹3 lakh" in s.subtitle


def test_falls_back_to_generic_without_subgroup_direction():
    # No per-class direction to lean on (e.g. summaries unavailable) → generic.
    totals = _totals(
        funds_to_buy_count=1,
        funds_to_sell_count=1,
        total_buy_inr=100_000.0,
        total_sell_inr=100_000.0,
    )
    s = build_rebalance_summary(totals, [], [])
    assert s.title == "Fine-tuning your mix"
    assert "2 trades" in s.subtitle
    assert s.reason == "Your mix has drifted from what your goals call for."


def test_headline_weightage_follows_lookthrough_breakdown_when_supplied():
    # Regression: the subgroup rollup lumps a held/recommended multi_asset sleeve
    # 100% into Equity, so it would quote a tiny ~2% equity trim. The look-through
    # breakdown (what the bars show) splits it, so the real swing is ~17%. When the
    # breakdown is supplied, the headline must quote it — not the subgroup rollup.
    totals = _totals(funds_to_sell_count=3, total_tax_estimate_inr=454_000.0)
    subs = [
        _sub("Equity", rebalance_inr=-505_000.0, sell=505_000.0, cur=28_585_000.0, final=28_080_000.0),
        _sub("Debt", rebalance_inr=505_000.0, buy=505_000.0, cur=43_000.0, final=548_000.0),
    ]
    bd = _breakdown(
        [
            ("Equity", 28_585_120.0, 23_763_754.0),  # 99.8% → 83.0%
            ("Debt", 43_458.0, 3_622_833.0),
            ("Others", 0.0, 1_241_653.0),
        ]
    )
    s = build_rebalance_summary(totals, subs, [], bd)
    assert s.title == "Trimming your Equity back to target"
    assert "Reducing your equity weightage by 17%" in s.subtitle
    assert "2%" not in s.subtitle  # NOT the subgroup-rollup number


def test_headline_falls_back_to_subgroups_without_breakdown():
    # Old runs (no breakdown) keep the prior subgroup-rollup behaviour.
    totals = _totals(funds_to_sell_count=2, total_tax_estimate_inr=0.0)
    subs = [
        _sub("Equity", rebalance_inr=-700_000.0, sell=700_000.0, cur=6_200_000.0, final=5_500_000.0),
        _sub("Debt", rebalance_inr=700_000.0, buy=700_000.0, cur=3_800_000.0, final=4_500_000.0),
    ]
    s = build_rebalance_summary(totals, subs, [], None)
    assert s.title == "Trimming your Equity back to target"
    assert "Reducing your equity weightage by 7%" in s.subtitle
