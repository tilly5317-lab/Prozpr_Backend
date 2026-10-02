"""Unit tests for the additional-investment engine input builder. The only collaborator is the fund-ranking CSV (an in-memory stand-in) plus a stand-in allocation output."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.domains.additional_investment.services.ainv_engine import input_builder as ib
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    build_additional_investment_input_for_user,
)


# ── stand-ins ──────────────────────────────────────────────────────────────
@dataclass
class _Row:
    """Stand-in for AggregatedSubgroupRow (only .subgroup + .model_dump consumed)."""

    subgroup: str
    emergency: float = 0.0
    short_term: float = 0.0
    medium_term: float = 0.0
    long_term: float = 0.0
    total: float = 0.0

    def model_dump(self) -> dict:
        return {
            "subgroup": self.subgroup,
            "emergency": self.emergency,
            "short_term": self.short_term,
            "medium_term": self.medium_term,
            "long_term": self.long_term,
            "total": self.total,
        }


@dataclass
class _RankRow:
    """Stand-in for the T2-extended FundRankRow (carries scheme_code)."""

    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    fund_name: str


# ── helpers ────────────────────────────────────────────────────────────────
def _alloc(rows) -> SimpleNamespace:
    """Stand-in for the practical-allocation output: just the per-subgroup rows.
    The builder reads no corpus total — the per-fund caps key off the deploy
    amount, so there is no existing-corpus field to stand in for."""
    return SimpleNamespace(aggregated_subgroups=rows)


def _patch(monkeypatch, *, ranking=None):
    """Patch the fund-ranking CSV loader — the builder's only collaborator."""
    monkeypatch.setattr(ib, "get_fund_ranking", lambda: ranking or {})


# ── tests ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_subgroups_map_all_rows_and_set_exclude(monkeypatch):
    """ALL practical-allocation rows (incl. the synthetic one) pass through
    verbatim — the 6 bucket fields map 1:1 — and the builder hands the engine
    ``exclude_subgroups`` so IT drops the synthetic row from the split."""
    rows = [
        _Row("large_cap", long_term=300000.0, total=300000.0),
        _Row("short_debt", short_term=100000.0, total=100000.0),
        _Row("tax_efficient_equities", long_term=50000.0, total=50000.0),
    ]
    _patch(monkeypatch)

    inp, _debug = await build_additional_investment_input_for_user(
        _alloc(rows), deploy_amount_inr=100000.0, cadence=ib.Cadence.LUMPSUM
    )

    # No hand-drop: all three rows are present in subgroups.
    assert [s.subgroup for s in inp.subgroups] == [
        "large_cap",
        "short_debt",
        "tax_efficient_equities",
    ]
    lc = next(s for s in inp.subgroups if s.subgroup == "large_cap")
    assert lc.long_term == 300000.0
    assert lc.total == 300000.0

    # The engine drops the synthetic row via exclude_subgroups (zero weight).
    assert inp.exclude_subgroups == {"tax_efficient_equities"}


@pytest.mark.asyncio
async def test_ranked_funds_flattened_with_scheme_code(monkeypatch):
    """get_fund_ranking() is flattened across subgroups; scheme_code carries through."""
    ranking = {
        "large_cap": [
            _RankRow("large_cap", "Large Cap Fund", 1, "INF_LC1", "120001", "LC One"),
            _RankRow("large_cap", "Large Cap Fund", 2, "INF_LC2", "120002", "LC Two"),
        ],
        "short_debt": [
            _RankRow("short_debt", "Low Duration", 1, "INF_SD1", "120003", "SD One"),
        ],
    }
    _patch(monkeypatch, ranking=ranking)

    inp, _ = await build_additional_investment_input_for_user(
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=50000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert len(inp.ranked_funds) == 3
    by_isin = {r.isin: r for r in inp.ranked_funds}
    assert by_isin["INF_LC1"].scheme_code == "120001"
    assert by_isin["INF_LC1"].rank == 1
    assert by_isin["INF_LC1"].recommended_fund == "LC One"
    assert by_isin["INF_SD1"].asset_subgroup == "short_debt"


@pytest.mark.asyncio
async def test_caps_use_cap_pct_for_and_others_default(monkeypatch):
    """cap_pct_by_subgroup routes through cap_pct_for; default_cap_pct is OTHERS_FUND_CAP_PCT."""
    _patch(monkeypatch)

    inp, _ = await build_additional_investment_input_for_user(
        _alloc(
            [
                _Row("large_cap", long_term=1.0, total=1.0),
                _Row("short_debt", short_term=1.0, total=1.0),
            ]
        ),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.cap_pct_by_subgroup["large_cap"] == ib.cap_pct_for("large_cap")
    assert inp.cap_pct_by_subgroup["short_debt"] == ib.cap_pct_for("short_debt")
    assert inp.default_cap_pct == ib.OTHERS_FUND_CAP_PCT


@pytest.mark.asyncio
async def test_investable_corpus_passthrough_and_cap_floors(monkeypatch):
    from additional_investment.models import Cadence

    _patch(monkeypatch, ranking={})
    rows = [_Row(subgroup="large_cap_equities", long_term=100.0, total=100.0)]

    inp, _ = await build_additional_investment_input_for_user(
        _alloc(rows),
        deploy_amount_inr=5000.0, cadence=Cadence.SIP_MONTHLY,
        investable_corpus_inr=6_000_000.0,
    )
    assert inp.investable_corpus_inr == 6_000_000.0
    # Cap floors still populated on the model (vestigial since spec 2026-09-24).
    assert inp.sip_fund_cap_floor_inr == ib.AINV_SIP_FUND_CAP_FLOOR_INR
    assert inp.sip_fund_cap_floor_inr == 10000.0  # default; env-overridable
    assert inp.lumpsum_fund_cap_floor_inr == ib.AINV_LUMPSUM_FUND_CAP_FLOOR_INR
    assert inp.lumpsum_fund_cap_floor_inr == 40000.0  # default; env-overridable

    inp2, _ = await build_additional_investment_input_for_user(
        _alloc(rows),
        deploy_amount_inr=5000.0, cadence=Cadence.SIP_MONTHLY,
    )
    assert inp2.investable_corpus_inr == 0.0  # default when caller omits it


@pytest.mark.asyncio
async def test_goal_share_reaches_the_engine_input(monkeypatch):
    _patch(monkeypatch)
    inp, debug = await build_additional_investment_input_for_user(
        _alloc([_Row("low_beta_equities", long_term=1.0, total=1.0)]),
        deploy_amount_inr=25000.0,
        cadence=ib.Cadence.SIP_MONTHLY,
        current_value_by_subgroup={"low_beta_equities": 100000.0},
        goal_share_inr=10000.0,
        goal_subgroup="arbitrage",
    )
    assert inp.goal_share_inr == 10000.0
    assert inp.goal_subgroup == "arbitrage"
    assert inp.current_value_by_subgroup is None
    assert debug["deployment_mode"] == "long_term"


@pytest.mark.asyncio
async def test_lumpsum_keeps_the_holdings_map(monkeypatch):
    _patch(monkeypatch)
    inp, debug = await build_additional_investment_input_for_user(
        _alloc([_Row("low_beta_equities", long_term=1.0, total=1.0)]),
        deploy_amount_inr=500000.0,
        cadence=ib.Cadence.LUMPSUM,
        current_value_by_subgroup={"low_beta_equities": 100000.0},
    )
    assert inp.current_value_by_subgroup == {"low_beta_equities": 100000.0}
    assert inp.goal_share_inr == 0.0
    assert debug["deployment_mode"] == "deficit_fill"


def _funding(to_goals=15000.0, from_corpus=900000.0, subgroup="arbitrage"):
    return SimpleNamespace(
        monthly_sip_to_goals=to_goals, from_corpus=from_corpus, asset_subgroup=subgroup,
    )


def test_goal_share_for_sip_is_the_monthly_goal_share():
    assert ib.goal_share_for(_funding(), ib.Cadence.SIP_MONTHLY, 25000.0) == (15000.0, "arbitrage")


def test_goal_share_for_lumpsum_is_from_corpus_capped_at_deploy():
    assert ib.goal_share_for(_funding(), ib.Cadence.LUMPSUM, 500000.0) == (500000.0, "arbitrage")


def test_goal_share_for_a_preference_run_is_zero():
    assert ib.goal_share_for(None, ib.Cadence.SIP_MONTHLY, 25000.0) == (0.0, None)


def _practical_goal_funding(monthly_sip: float):
    """Run the real practical-allocation engine: one short-term goal (12
    months, ₹6L FV), no short-term holdings, varying monthly_sip."""
    from asset_allocation_pydantic.models import Goal
    from practical_asset_allocation.pipeline import (
        PracticalAllocationInput,
        run_practical_allocation,
    )

    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000,
        osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
        total_corpus=20_000_000.0, monthly_household_expense=100_000,
        effective_tax_rate=15.0, elss_corpus=0.0,
        short_term_holdings=0.0, monthly_sip=monthly_sip,
        goals=[
            Goal(
                goal_name="short-term goal",
                time_to_goal_months=12,
                amount_needed=600_000,
                amount_needed_fv=600_000,
                goal_priority="non_negotiable",
            )
        ],
    )
    return run_practical_allocation(inp).goal_funding


def test_goal_share_for_e2e_lumpsum_funds_the_full_gap_with_no_assumed_sip():
    """End-to-end: a real practical-allocation run's own goal_funding, fed
    straight into goal_share_for, proves the lumpsum path (monthly_sip=0.0)
    gives the short-term goal its full remaining need — a monthly_sip of
    50_000 (today's replaced behaviour) would instead cover the ₹6L/12mo goal
    by itself, leaving goal_share_for nothing from the corpus."""
    funding_no_sip = _practical_goal_funding(monthly_sip=0.0)
    share, subgroup = ib.goal_share_for(funding_no_sip, ib.Cadence.LUMPSUM, 500_000.0)
    assert (share, subgroup) == (500_000.0, funding_no_sip.asset_subgroup)

    funding_with_sip = _practical_goal_funding(monthly_sip=50_000.0)
    share, subgroup = ib.goal_share_for(funding_with_sip, ib.Cadence.LUMPSUM, 500_000.0)
    assert (share, subgroup) == (0.0, funding_with_sip.asset_subgroup)
