"""Sectioned markdown output. Friend voice. Pure-Python."""

from decimal import Decimal


def _make_minimal_response(*, with_warnings=False, tax_zero=True, with_actions=False):
    """Build a small valid RebalancingComputeResponse for assertion-level tests."""
    from datetime import datetime
    from uuid import uuid4

    from app.services.ai_bridge.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from Rebalancing.models import (  # type: ignore[import-not-found]
        FundRowAfterStep5,
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
        RebalancingWarning,
        SubgroupSummary,
        WarningCode,
    )

    totals = RebalancingTotals(
        total_buy_inr=Decimal("100"),
        total_sell_inr=Decimal("100"),
        net_cash_flow_inr=Decimal(0),
        total_stcg_realised=Decimal(0) if tax_zero else Decimal("50"),
        total_ltcg_realised=Decimal(0),
        total_stcg_net_off=Decimal(0),
        total_tax_estimate_inr=Decimal(0) if tax_zero else Decimal("10"),
        total_exit_load_inr=Decimal(0),
        unrebalanced_remainder_inr=Decimal(0),
        rows_count=1,
        funds_to_buy_count=1,
        funds_to_sell_count=0,
        funds_to_exit_count=0,
        funds_held_count=0,
    )

    subgroups = []
    if with_actions:
        # Two subgroups in the same SEBI category ("Large Cap Fund") would
        # collapse to one section; use two distinct sub_categories so the
        # grouping logic is exercised.
        action_a = FundRowAfterStep5(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="Test Large Cap Fund",
            isin="INFTEST0001",
            rank=1,
            target_amount_pre_cap=Decimal("500"),
            present_allocation_inr=Decimal(0),
            current_nav=Decimal("100"),
            max_pct=10.0,
            target_pre_cap_pct=50.0,
            target_own_capped_pct=50.0,
            final_target_pct=50.0,
            final_target_amount=Decimal("500"),
            diff=Decimal("500"),
            exit_flag=False,
            worth_to_change=True,
            stcg_amount=Decimal(0),
            ltcg_amount=Decimal(0),
            exit_load_amount=Decimal(0),
            pass1_buy_amount=Decimal("500"),
            pass1_underbuy_amount=Decimal(0),
            pass1_sell_amount=Decimal(0),
            pass1_undersell_amount=Decimal(0),
            pass1_sell_lt_amount=Decimal(0),
            pass1_realised_ltcg=Decimal(0),
            pass1_sell_st_amount=Decimal(0),
            pass1_realised_stcg=Decimal(0),
            stcg_budget_remaining_after_pass1=Decimal(0),
            pass1_sell_amount_no_stcg_cap=Decimal(0),
            pass1_undersell_due_to_stcg_cap=Decimal(0),
            pass1_blocked_stcg_value=Decimal(0),
            holding_after_initial_trades=Decimal("500"),
            stcg_offset_amount=Decimal(0),
            pass2_sell_amount=Decimal(0),
            pass2_undersell_amount=Decimal(0),
            final_holding_amount=Decimal("500"),
        )
        action_b = FundRowAfterStep5(
            asset_subgroup="medium_beta_equities",
            sub_category="Flexi Cap Fund",
            recommended_fund="Test Flexi Cap Fund",
            isin="INFTEST0002",
            rank=2,
            target_amount_pre_cap=Decimal(0),
            present_allocation_inr=Decimal("400"),
            current_nav=Decimal("100"),
            max_pct=10.0,
            target_pre_cap_pct=0.0,
            target_own_capped_pct=0.0,
            final_target_pct=0.0,
            final_target_amount=Decimal(0),
            diff=Decimal("-400"),
            exit_flag=True,
            worth_to_change=True,
            stcg_amount=Decimal(0),
            ltcg_amount=Decimal(0),
            exit_load_amount=Decimal(0),
            pass1_buy_amount=Decimal(0),
            pass1_underbuy_amount=Decimal(0),
            pass1_sell_amount=Decimal("400"),
            pass1_undersell_amount=Decimal(0),
            pass1_sell_lt_amount=Decimal(0),
            pass1_realised_ltcg=Decimal(0),
            pass1_sell_st_amount=Decimal(0),
            pass1_realised_stcg=Decimal(0),
            stcg_budget_remaining_after_pass1=Decimal(0),
            pass1_sell_amount_no_stcg_cap=Decimal(0),
            pass1_undersell_due_to_stcg_cap=Decimal(0),
            pass1_blocked_stcg_value=Decimal(0),
            holding_after_initial_trades=Decimal(0),
            stcg_offset_amount=Decimal(0),
            pass2_sell_amount=Decimal(0),
            pass2_undersell_amount=Decimal(0),
            final_holding_amount=Decimal(0),
        )
        subgroups = [
            SubgroupSummary(
                asset_subgroup="low_beta_equities",
                goal_target_inr=Decimal("500"),
                current_holding_inr=Decimal(0),
                suggested_final_holding_inr=Decimal("500"),
                rebalance_inr=Decimal("500"),
                total_buy_inr=Decimal("500"),
                total_sell_inr=Decimal(0),
                ranks_total=1,
                ranks_with_holding=0,
                ranks_with_action=1,
                actions=[action_a],
            ),
            SubgroupSummary(
                asset_subgroup="medium_beta_equities",
                goal_target_inr=Decimal(0),
                current_holding_inr=Decimal("400"),
                suggested_final_holding_inr=Decimal(0),
                rebalance_inr=Decimal("-400"),
                total_buy_inr=Decimal(0),
                total_sell_inr=Decimal("400"),
                ranks_total=1,
                ranks_with_holding=1,
                ranks_with_action=1,
                actions=[action_b],
            ),
        ]
    warnings = []
    if with_warnings:
        warnings.append(RebalancingWarning(
            code=WarningCode.UNREBALANCED_REMAINDER,
            message="₹500 unrebalanced",
            affected_isins=[],
        ))
    knobs = KnobSnapshot(
        multi_fund_cap_pct=20.0,
        others_fund_cap_pct=10.0,
        rebalance_min_change_pct=0.10,
        exit_floor_rating=5,
        ltcg_annual_exemption_inr=Decimal("125000"),
        stcg_rate_equity_pct=20.0,
        ltcg_rate_equity_pct=12.5,
        st_threshold_months_equity=12,
        st_threshold_months_debt=24,
        multi_cap_sub_categories=[],
    )
    metadata = RebalancingRunMetadata(
        computed_at=datetime(2026, 4, 28, 12, 0, 0),
        engine_version="1.0.0",
        request_corpus_inr=Decimal("1000"),
        knob_snapshot=knobs,
        request_id=uuid4(),
    )
    return RebalancingComputeResponse(
        rows=[],
        subgroups=subgroups,
        totals=totals,
        metadata=metadata,
        trade_list=[],
        warnings=warnings,
    )


def test_output_includes_lead_line_when_allocation_refreshed():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=False)
    assert "asset mix" in text.lower()


def test_output_omits_lead_line_when_cache_hit():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "asset mix" not in text.lower()


def test_output_includes_corpus_in_header():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "1,000" in text or "1000" in text


def test_tax_line_omitted_when_zero():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(tax_zero=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "trade-offs" not in text.lower()


def test_tax_line_present_when_nonzero():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(tax_zero=False)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "trade-offs" in text.lower()


def test_heads_up_section_present_when_warnings():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(with_warnings=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "heads-up" in text.lower()


def test_closing_line_always_present():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response()
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "sanity check" in text.lower()


def test_summary_table_present_when_actions():
    """Top summary table renders with markdown pipes when there are actions."""
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(with_actions=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "| Category | Current | Target | Plan |" in text


def test_uses_sebi_sub_category_labels_not_asset_subgroup_keys():
    """Per-section headers use SEBI sub_category like 'Large Cap Fund', not 'low_beta_equities'."""
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(with_actions=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "Large Cap Fund" in text
    assert "Flexi Cap Fund" in text
    assert "low_beta_equities" not in text
    assert "medium_beta_equities" not in text


def test_buy_table_renders_when_buys_present():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(with_actions=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "| Buy into | Amount |" in text
    assert "Test Large Cap Fund" in text


def test_sell_table_renders_with_exit_verb():
    from app.services.ai_bridge.rebalancing.formatter import (
        format_rebalancing_chat_brief,
    )

    response = _make_minimal_response(with_actions=True)
    text = format_rebalancing_chat_brief(response, used_cached_allocation=True)
    assert "| Action | From | Amount |" in text
    # exit_flag=True on action_b → "Exit" verb
    assert "Exit" in text
    assert "Test Flexi Cap Fund" in text
