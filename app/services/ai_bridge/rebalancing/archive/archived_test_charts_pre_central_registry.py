"""Chart computers + LLM-driven picker for the rebalancing reply."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]
    FundRowAfterStep5,
    KnobSnapshot,
    RebalancingComputeResponse,
    RebalancingRunMetadata,
    RebalancingTotals,
    SubgroupSummary,
)


def _row(
    *,
    asset_subgroup: str,
    sub_category: str,
    fund: str,
    isin: str,
    rank: int,
    target: Decimal,
    present: Decimal,
    buy: Decimal = Decimal(0),
    sell: Decimal = Decimal(0),
    stcg: Decimal = Decimal(0),
    ltcg: Decimal = Decimal(0),
    exit_load_amount: Decimal = Decimal(0),
    exit_flag: bool = False,
) -> FundRowAfterStep5:
    return FundRowAfterStep5(
        asset_subgroup=asset_subgroup,
        sub_category=sub_category,
        recommended_fund=fund,
        isin=isin,
        rank=rank,
        target_amount_pre_cap=target,
        present_allocation_inr=present,
        current_nav=Decimal("100"),
        max_pct=10.0,
        target_pre_cap_pct=0.0,
        target_own_capped_pct=0.0,
        final_target_pct=0.0,
        final_target_amount=target,
        diff=target - present,
        exit_flag=exit_flag,
        worth_to_change=True,
        stcg_amount=stcg,
        ltcg_amount=ltcg,
        exit_load_amount=exit_load_amount,
        pass1_buy_amount=buy,
        pass1_underbuy_amount=Decimal(0),
        pass1_sell_amount=sell,
        pass1_undersell_amount=Decimal(0),
        pass1_sell_lt_amount=Decimal(0),
        pass1_realised_ltcg=ltcg,
        pass1_sell_st_amount=Decimal(0),
        pass1_realised_stcg=stcg,
        stcg_budget_remaining_after_pass1=Decimal(0),
        pass1_sell_amount_no_stcg_cap=Decimal(0),
        pass1_undersell_due_to_stcg_cap=Decimal(0),
        pass1_blocked_stcg_value=Decimal(0),
        holding_after_initial_trades=present - sell + buy,
        stcg_offset_amount=Decimal(0),
        pass2_sell_amount=Decimal(0),
        pass2_undersell_amount=Decimal(0),
        final_holding_amount=present - sell + buy,
    )


def _response(
    *,
    rows_per_subgroup: dict[tuple[str, Decimal, Decimal, Decimal], list[FundRowAfterStep5]],
    tax_estimate: Decimal = Decimal("100"),
    exit_load: Decimal = Decimal("50"),
) -> RebalancingComputeResponse:
    """Wrap a small set of subgroups into a valid response.

    Key shape: (asset_subgroup, goal_target, current, final). Values are the
    list of FundRowAfterStep5 rows that belong to that subgroup.
    """
    subgroups = []
    for (asset_subgroup, goal, current, final), rows in rows_per_subgroup.items():
        subgroups.append(SubgroupSummary(
            asset_subgroup=asset_subgroup,
            goal_target_inr=goal,
            current_holding_inr=current,
            suggested_final_holding_inr=final,
            rebalance_inr=final - current,
            total_buy_inr=sum((r.pass1_buy_amount or Decimal(0)) for r in rows),
            total_sell_inr=sum((r.pass1_sell_amount or Decimal(0)) for r in rows),
            ranks_total=len(rows),
            ranks_with_holding=sum(1 for r in rows if r.present_allocation_inr > 0),
            ranks_with_action=sum(
                1 for r in rows
                if (r.pass1_buy_amount or Decimal(0)) > 0
                or (r.pass1_sell_amount or Decimal(0)) > 0
            ),
            actions=rows,
        ))

    knobs = KnobSnapshot(
        multi_fund_cap_pct=20.0, others_fund_cap_pct=10.0,
        rebalance_min_change_pct=0.10, exit_floor_rating=5,
        ltcg_annual_exemption_inr=Decimal("125000"),
        stcg_rate_equity_pct=20.0, ltcg_rate_equity_pct=12.5,
        st_threshold_months_equity=12, st_threshold_months_debt=24,
        multi_cap_sub_categories=[],
    )
    return RebalancingComputeResponse(
        rows=[],
        subgroups=subgroups,
        totals=RebalancingTotals(
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            net_cash_flow_inr=Decimal(0),
            total_stcg_realised=Decimal(0),
            total_ltcg_realised=Decimal(0),
            total_stcg_net_off=Decimal(0),
            total_tax_estimate_inr=tax_estimate,
            total_exit_load_inr=exit_load,
            unrebalanced_remainder_inr=Decimal(0),
            rows_count=0,
            funds_to_buy_count=1,
            funds_to_sell_count=0,
            funds_to_exit_count=0,
            funds_held_count=0,
        ),
        metadata=RebalancingRunMetadata(
            computed_at=datetime(2026, 4, 28),
            engine_version="test-1.0.0",
            request_corpus_inr=Decimal("1000"),
            knob_snapshot=knobs,
            request_id=uuid4(),
        ),
        trade_list=[],
        warnings=[],
    )


# ── Computer tests ──


def test_category_gap_bar_orders_by_largest_gap():
    from app.services.ai_bridge.rebalancing.charts import compute_category_gap_bar

    rows_lc = [_row(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund",
                    fund="LC1", isin="X1", rank=1, target=Decimal("500"),
                    present=Decimal("100"), buy=Decimal("400"))]
    rows_mc = [_row(asset_subgroup="multi_asset", sub_category="Multi Cap Fund",
                    fund="MC1", isin="X2", rank=1, target=Decimal("200"),
                    present=Decimal("180"), buy=Decimal("20"))]
    response = _response(rows_per_subgroup={
        ("low_beta_equities", Decimal("500"), Decimal("100"), Decimal("500")): rows_lc,
        ("multi_asset", Decimal("200"), Decimal("180"), Decimal("200")): rows_mc,
    })

    spec = compute_category_gap_bar(response)
    assert spec is not None
    assert spec.chart_type == "category_gap_bar"
    # Largest gap (Large Cap: 500-100=400) should lead.
    assert spec.data["categories"][0] == "Large Cap Fund"
    assert spec.data["categories"][1] == "Multi Cap Fund"
    series_names = [s["name"] for s in spec.data["series"]]
    assert series_names == ["Current", "Target", "Plan"]


def test_planned_donut_drops_zero_finals():
    from app.services.ai_bridge.rebalancing.charts import compute_planned_donut

    rows_lc = [_row(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund",
                    fund="LC1", isin="X1", rank=1, target=Decimal("500"),
                    present=Decimal("0"), buy=Decimal("500"))]
    rows_exit = [_row(asset_subgroup="medium_beta_equities", sub_category="Flexi Cap Fund",
                      fund="FX1", isin="X2", rank=1, target=Decimal("0"),
                      present=Decimal("100"), sell=Decimal("100"), exit_flag=True)]
    response = _response(rows_per_subgroup={
        ("low_beta_equities", Decimal("500"), Decimal("0"), Decimal("500")): rows_lc,
        ("medium_beta_equities", Decimal("0"), Decimal("100"), Decimal("0")): rows_exit,
    })

    spec = compute_planned_donut(response)
    assert spec is not None
    labels = [s["label"] for s in spec.data["slices"]]
    # Flexi Cap (final=0) is dropped; Large Cap (final=500) stays.
    assert labels == ["Large Cap Fund"]


def test_tax_cost_bar_returns_none_when_zero_costs():
    from app.services.ai_bridge.rebalancing.charts import compute_tax_cost_bar

    rows = [_row(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund",
                 fund="LC1", isin="X1", rank=1, target=Decimal("500"),
                 present=Decimal("0"), buy=Decimal("500"))]
    response = _response(
        rows_per_subgroup={
            ("low_beta_equities", Decimal("500"), Decimal("0"), Decimal("500")): rows,
        },
        tax_estimate=Decimal(0),
        exit_load=Decimal(0),
    )

    assert compute_tax_cost_bar(response) is None


def test_tax_cost_bar_includes_realised_gains_per_category():
    from app.services.ai_bridge.rebalancing.charts import compute_tax_cost_bar

    rows = [_row(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund",
                 fund="LC1", isin="X1", rank=1, target=Decimal("0"),
                 present=Decimal("100"), sell=Decimal("100"),
                 stcg=Decimal("10"), ltcg=Decimal("20"),
                 exit_load_amount=Decimal("5"))]
    response = _response(rows_per_subgroup={
        ("low_beta_equities", Decimal(0), Decimal("100"), Decimal(0)): rows,
    })

    spec = compute_tax_cost_bar(response)
    assert spec is not None
    series = {s["name"]: s["values"] for s in spec.data["series"]}
    assert series["Short-term gains"] == [10.0]
    assert series["Long-term gains"] == [20.0]
    # exit_load is apportioned by sold/present; sold == present here so full value.
    assert series["Exit load"] == [5.0]


def test_available_charts_skips_none_outputs():
    from app.services.ai_bridge.rebalancing.charts import available_charts

    rows = [_row(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund",
                 fund="LC1", isin="X1", rank=1, target=Decimal("500"),
                 present=Decimal("0"), buy=Decimal("500"))]
    response = _response(
        rows_per_subgroup={
            ("low_beta_equities", Decimal("500"), Decimal("0"), Decimal("500")): rows,
        },
        tax_estimate=Decimal(0),
        exit_load=Decimal(0),
    )

    charts = available_charts(response)
    types = [c.chart_type for c in charts]
    assert "category_gap_bar" in types
    assert "planned_donut" in types
    # tax_cost_bar dropped because totals are zero.
    assert "tax_cost_bar" not in types


# ── Picker tests ──


@pytest.mark.asyncio
async def test_pick_chart_returns_none_on_empty(monkeypatch):
    # Re-enable the real picker for this test by overriding the autouse stub.
    import app.services.ai_bridge.rebalancing.chart_picker as cp

    monkeypatch.undo()  # opt out of any other monkeypatch attempts
    result = await cp.pick_chart([], "anything")
    assert result is None


@pytest.mark.asyncio
async def test_pick_chart_short_circuits_single_candidate():
    from app.services.ai_bridge.rebalancing.chart_picker import pick_chart
    from app.services.ai_bridge.rebalancing.charts import ChartSpec

    only = ChartSpec(chart_type="planned_donut", title="t", data={"slices": []})
    # No LLM call should happen with a single candidate.
    result = await pick_chart([only], "what does my portfolio look like after?")
    assert result is only


@pytest.mark.asyncio
async def test_pick_chart_uses_llm_choice_when_available(monkeypatch):
    """When the LLM returns a valid chart_type, the picker uses it."""
    from app.services.ai_bridge.rebalancing import chart_picker as cp
    from app.services.ai_bridge.rebalancing.charts import ChartSpec

    candidates = [
        ChartSpec(chart_type="category_gap_bar", title="gap"),
        ChartSpec(chart_type="planned_donut", title="donut"),
        ChartSpec(chart_type="tax_cost_bar", title="cost"),
    ]

    class _FakeChoice:
        chart_type = "tax_cost_bar"
        reason = "user asked about cost"

    async def _fake_ainvoke(llm, system_text, user_text):
        return _FakeChoice()

    # Skip the real ChatAnthropic construction by patching _ainvoke.
    monkeypatch.setattr(cp, "_ainvoke", _fake_ainvoke)
    # Patch ChatAnthropic so .with_structured_output(...) returns a stand-in
    # object that _fake_ainvoke ignores anyway.
    monkeypatch.setattr(
        cp, "ChatAnthropic",
        lambda **_: type("Stub", (), {
            "with_structured_output": lambda self, _model: object(),
        })(),
    )

    result = await cp.pick_chart(candidates, "is this rebalance worth the cost?")
    assert result is not None
    assert result.chart_type == "tax_cost_bar"


@pytest.mark.asyncio
async def test_pick_chart_falls_back_when_llm_raises(monkeypatch):
    from app.services.ai_bridge.rebalancing import chart_picker as cp
    from app.services.ai_bridge.rebalancing.charts import ChartSpec

    candidates = [
        ChartSpec(chart_type="category_gap_bar", title="gap"),
        ChartSpec(chart_type="planned_donut", title="donut"),
    ]

    async def _boom(llm, system_text, user_text):
        raise RuntimeError("network down")

    monkeypatch.setattr(cp, "_ainvoke", _boom)
    monkeypatch.setattr(
        cp, "ChatAnthropic",
        lambda **_: type("Stub", (), {
            "with_structured_output": lambda self, _model: object(),
        })(),
    )

    result = await cp.pick_chart(candidates, "rebalance my portfolio")
    # First candidate is the silent-fail fallback.
    assert result is candidates[0]
