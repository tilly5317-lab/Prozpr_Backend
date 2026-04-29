"""Sectioned markdown output. Friend voice. Pure-Python."""

from decimal import Decimal


def _make_minimal_response(*, with_warnings=False, tax_zero=True):
    """Build a small valid RebalancingComputeResponse for assertion-level tests."""
    from datetime import datetime
    from uuid import uuid4

    from app.services.ai_bridge.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from Rebalancing.models import (  # type: ignore[import-not-found]
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
        RebalancingWarning,
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
        subgroups=[],
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
