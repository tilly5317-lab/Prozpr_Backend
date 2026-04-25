from __future__ import annotations

from decimal import Decimal

from Rebalancing import run_rebalancing


def test_response_shape(request_with_holdings):
    resp = run_rebalancing(request_with_holdings)

    assert resp.metadata.engine_version == "1.0.0"
    assert resp.metadata.knob_snapshot.exit_floor_rating == 5
    assert resp.totals.rows_count == len(request_with_holdings.rows)

    # Σ rows arithmetic-consistent with totals.
    sum_buys = sum(r.pass1_buy_amount for r in resp.rows)
    sum_sells = sum(r.pass1_sell_amount + r.pass2_sell_amount for r in resp.rows)
    assert resp.totals.total_buy_inr == sum_buys
    assert resp.totals.total_sell_inr == sum_sells


def test_trade_list_drops_holds(request_with_holdings):
    resp = run_rebalancing(request_with_holdings)
    isins_in_trades = {t.isin for t in resp.trade_list}
    for r in resp.rows:
        net_movement = r.pass1_buy_amount + r.pass1_sell_amount + r.pass2_sell_amount
        if net_movement == 0:
            assert r.isin not in isins_in_trades


def test_bad_fund_gets_exit_action(request_with_holdings):
    resp = run_rebalancing(request_with_holdings)
    bad_trade = next((t for t in resp.trade_list if t.isin == "BAD1"), None)
    assert bad_trade is not None
    assert bad_trade.action == "EXIT"
    assert bad_trade.reason_code == "exit_bad_fund"


def test_amount_is_positive_in_trade_list(request_with_holdings):
    resp = run_rebalancing(request_with_holdings)
    for t in resp.trade_list:
        assert t.amount_inr > Decimal(0)


def test_trade_action_carries_rationale(request_with_holdings):
    resp = run_rebalancing(request_with_holdings)
    for t in resp.trade_list:
        assert t.reason_code != ""
        assert t.reason_title != ""
        # reason_text may be empty for unmapped codes (defensive); for codes
        # the engine produces it must always be populated.
        assert t.reason_text != "", f"Missing rationale text for {t.reason_code}"


def test_subgroups_summarize_rows(request_with_holdings):
    resp = run_rebalancing(request_with_holdings)
    # Every active subgroup must appear; totals must be arithmetic-consistent.
    assert len(resp.subgroups) > 0
    for s in resp.subgroups:
        # ranks_with_action <= ranks_total
        assert s.ranks_with_action <= s.ranks_total
        # rebalance = suggested_final - current
        assert s.rebalance_inr == (
            s.suggested_final_holding_inr - s.current_holding_inr
        )
        # actions list size matches the count
        assert len(s.actions) == s.ranks_with_action
        # All listed actions belong to this subgroup
        for a in s.actions:
            assert a.asset_subgroup == s.asset_subgroup
