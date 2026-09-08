"""Unit tests for the additional-investment engine input builder.

Mirrors rebal_engine/tests/test_input_builder.py, but the engine is now
HOLDING-AGNOSTIC: the only collaborators are the fund-ranking CSV and the
cashflow projection (both replaced with plain in-memory stand-ins), plus a
stand-in allocation output. There is no DB ledger / NAV / classifier path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
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


@dataclass
class _Goal:
    goal_date: date
    is_funded: bool


# ── helpers ────────────────────────────────────────────────────────────────
def _months_from_now(months: int) -> date:
    """A 1st-of-month date exactly `months` whole months ahead of today."""
    today = date.today()
    total = today.year * 12 + (today.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(user_ctx=SimpleNamespace(id=uuid.uuid4()))


def _alloc(rows) -> SimpleNamespace:
    """Stand-in for the practical-allocation output: just the per-subgroup rows.
    The builder reads no corpus total — the per-fund caps key off the deploy
    amount, so there is no existing-corpus field to stand in for."""
    return SimpleNamespace(aggregated_subgroups=rows)


def _patch(monkeypatch, *, ranking=None, goals=()):
    """Patch the only two real collaborators: the fund-ranking CSV loader and the
    cashflow projection. No ledger / NAV / classifier — the engine is
    holding-agnostic."""
    ranking = ranking or {}

    monkeypatch.setattr(ib, "get_fund_ranking", lambda: ranking)

    async def _fake_cashflow(user, *, anchor_date=None):
        return SimpleNamespace(goals=list(goals))

    monkeypatch.setattr(ib, "run_cashflow_projection_for_user", _fake_cashflow)


# ── tests ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_subgroups_map_all_rows_and_set_exclude(monkeypatch):
    """ALL practical-allocation rows (incl. the two synthetic ones) pass through
    verbatim — the 6 bucket fields map 1:1 — and the builder hands the engine
    ``exclude_subgroups`` so IT drops the synthetic rows from the split."""
    rows = [
        _Row("large_cap", long_term=300000.0, total=300000.0),
        _Row("short_debt", short_term=100000.0, total=100000.0),
        _Row("tax_efficient_equities", long_term=50000.0, total=50000.0),
        _Row("non_mf_equities", long_term=40000.0, total=40000.0),
    ]
    _patch(monkeypatch)

    inp, _debug = await build_additional_investment_input_for_user(
        _ctx(), _alloc(rows), deploy_amount_inr=100000.0, cadence=ib.Cadence.LUMPSUM
    )

    # No hand-drop: all four rows are present in subgroups.
    assert [s.subgroup for s in inp.subgroups] == [
        "large_cap",
        "short_debt",
        "tax_efficient_equities",
        "non_mf_equities",
    ]
    lc = next(s for s in inp.subgroups if s.subgroup == "large_cap")
    assert lc.long_term == 300000.0
    assert lc.total == 300000.0

    # The engine drops the synthetic rows via exclude_subgroups (zero weight).
    assert inp.exclude_subgroups == {"tax_efficient_equities", "non_mf_equities"}


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
        _ctx(),
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
async def test_short_term_unfunded_sets_flag_false(monkeypatch):
    """An unfunded <24-month goal -> short_term_fulfilled is False; with the only
    medium goal funded, medium_term_fulfilled stays True."""
    goals = [
        _Goal(_months_from_now(12), is_funded=False),  # short-term, unfunded
        _Goal(_months_from_now(48), is_funded=True),   # medium-term, funded
    ]
    _patch(monkeypatch, goals=goals)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.short_term_fulfilled is False
    assert inp.medium_term_fulfilled is True


@pytest.mark.asyncio
async def test_medium_term_unfunded_sets_flag_false(monkeypatch):
    """An unfunded 24–60-month goal -> medium_term_fulfilled is False; with the
    short goal funded, short_term_fulfilled stays True. (Long-term goal ignored.)"""
    goals = [
        _Goal(_months_from_now(12), is_funded=True),   # short-term, funded
        _Goal(_months_from_now(48), is_funded=False),  # medium-term, unfunded
        _Goal(_months_from_now(84), is_funded=False),  # long-term, ignored
    ]
    _patch(monkeypatch, goals=goals)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.short_term_fulfilled is True
    assert inp.medium_term_fulfilled is False


@pytest.mark.asyncio
async def test_both_flags_true_when_funded_or_none(monkeypatch):
    """A funded short goal + a funded medium goal -> both flags True (long-term
    goal ignored; True is also the no-goals default for each bucket)."""
    goals = [
        _Goal(_months_from_now(12), is_funded=True),   # short-term, funded
        _Goal(_months_from_now(48), is_funded=True),   # medium-term, funded
        _Goal(_months_from_now(84), is_funded=False),  # long-term, ignored
    ]
    _patch(monkeypatch, goals=goals)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.short_term_fulfilled is True
    assert inp.medium_term_fulfilled is True


@pytest.mark.asyncio
async def test_caps_use_cap_pct_for_and_others_default(monkeypatch):
    """cap_pct_by_subgroup routes through cap_pct_for; default_cap_pct is OTHERS_FUND_CAP_PCT."""
    _patch(monkeypatch)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
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


# ── deficit-fill path (lumpsum + holdings map; spec 2026-07-03) ─────────────
@pytest.mark.asyncio
async def test_deficit_map_skips_cashflow_projection(monkeypatch):
    """Lumpsum + holdings map -> the cashflow projection must NOT run (its only
    consumer here was the old nearest-unfunded label); flags stay False; the map
    is attached to the engine input; debug records the mode."""
    _patch(monkeypatch)

    async def _boom(user, asof):
        raise AssertionError("_goal_funding_flags must not run on the deficit path")

    monkeypatch.setattr(ib, "_goal_funding_flags", _boom)

    inp, debug = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("low_beta_equities", long_term=1.0, total=1.0)]),
        deploy_amount_inr=500000.0,
        cadence=ib.Cadence.LUMPSUM,
        current_value_by_subgroup={"low_beta_equities": 100000.0},
    )

    assert inp.current_value_by_subgroup == {"low_beta_equities": 100000.0}
    assert inp.short_term_fulfilled is False
    assert inp.medium_term_fulfilled is False
    assert debug["deployment_mode"] == "deficit_fill"


@pytest.mark.asyncio
async def test_sip_never_attaches_the_map_and_still_runs_flags(monkeypatch):
    """SIP + map -> the map is dropped (legacy path) and the goal-funding flags
    still come from the cashflow projection."""
    _patch(monkeypatch)

    async def _flags(user, asof):
        return True, False

    monkeypatch.setattr(ib, "_goal_funding_flags", _flags)

    inp, debug = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("low_beta_equities", long_term=1.0, total=1.0)]),
        deploy_amount_inr=25000.0,
        cadence=ib.Cadence.SIP_MONTHLY,
        current_value_by_subgroup={"low_beta_equities": 100000.0},  # must be dropped
    )

    assert inp.current_value_by_subgroup is None
    assert inp.short_term_fulfilled is True
    assert inp.medium_term_fulfilled is False
    assert debug["deployment_mode"] == "single_bucket"


@pytest.mark.asyncio
async def test_rebal_buys_passthrough_and_default(monkeypatch):
    from additional_investment.models import Cadence

    _patch(monkeypatch, ranking={}, goals=())
    rows = [_Row(subgroup="large_cap_equities", long_term=100.0, total=100.0)]
    rebal_map = {"large_cap_equities": ["INF001"]}

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(), _alloc(rows),
        deploy_amount_inr=5000.0, cadence=Cadence.SIP_MONTHLY,
        rebal_buy_isins_by_subgroup=rebal_map,
    )
    assert inp.rebal_buy_isins_by_subgroup == rebal_map
    # Per-fund cap floors wired from Rebalancing config (amendment 2026-07-06).
    assert inp.sip_fund_cap_floor_inr == ib.AINV_SIP_FUND_CAP_FLOOR_INR
    assert inp.sip_fund_cap_floor_inr == 10000.0  # default; env-overridable
    assert inp.lumpsum_fund_cap_floor_inr == ib.AINV_LUMPSUM_FUND_CAP_FLOOR_INR
    assert inp.lumpsum_fund_cap_floor_inr == 40000.0  # default; env-overridable

    inp2, _ = await build_additional_investment_input_for_user(
        _ctx(), _alloc(rows),
        deploy_amount_inr=5000.0, cadence=Cadence.SIP_MONTHLY,
    )
    assert inp2.rebal_buy_isins_by_subgroup is None
