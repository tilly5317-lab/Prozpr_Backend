"""Tests for build_mutual_fund_query_facts — assembles MutualFundQueryFacts from resolved funds.

The DB-touching returns helper is mocked, so the builder's own logic is tested in
isolation (no DB fixture needed)."""

from __future__ import annotations

import pytest

from app.domains.mutual_funds.services import mutual_fund_query_service as S
from app.domains.mutual_funds.services.fund_resolver_service import ResolvedFund
from app.domains.rebalancing.services.rebal_engine.fund_rank import FundRankRow


@pytest.fixture(autouse=True)
def _fake_cagr(monkeypatch):
    async def fake(db, scheme_code):
        return {"return_1y_cagr_pct": 10.0, "return_3y_cagr_pct": 15.0, "return_5y_cagr_pct": 12.0}
    monkeypatch.setattr(S, "trailing_cagr_for_scheme", fake)


def _row(isin, name, sub="Flexi Cap Fund", rank=1, code="C1"):
    return FundRankRow(
        asset_subgroup="medium_beta_equities", sub_category=sub, rank=rank,
        isin=isin, fund_name=name, selection_reason="long track record", scheme_code=code,
    )


@pytest.mark.asyncio
async def test_single_in_shortlist_has_house_view(monkeypatch):
    monkeypatch.setattr(S, "ranking_by_isin", lambda isin: _row("INF001", "Parag Parikh Flexi Cap"))
    resolved = [ResolvedFund("119771", "Parag Parikh Flexi Cap - Direct - Growth", "INF001")]

    facts = await S.build_mutual_fund_query_facts(None, resolved, "reasoning")

    f = facts.funds[0]
    assert f.has_house_view is True
    assert f.house_reason == "long track record"
    assert f.returns.return_3y_cagr_pct == 15.0


@pytest.mark.asyncio
async def test_single_not_in_shortlist_returns_only(monkeypatch):
    monkeypatch.setattr(S, "ranking_by_isin", lambda isin: None)
    resolved = [ResolvedFund("999", "Some Fund - Direct - Growth", "INF999")]

    facts = await S.build_mutual_fund_query_facts(None, resolved, "returns")

    f = facts.funds[0]
    assert f.has_house_view is False
    assert f.house_reason is None
    assert f.returns.return_1y_cagr_pct == 10.0


@pytest.mark.asyncio
async def test_single_comparison_populates_peers_self_excluded(monkeypatch):
    monkeypatch.setattr(S, "ranking_by_isin", lambda isin: _row("INF001", "Parag Parikh Flexi Cap"))
    monkeypatch.setattr(S, "peers_by_sub_category",
                        lambda sub, exclude_isin: [_row("INF002", "HDFC Flexi Cap", rank=2, code="C2")])
    resolved = [ResolvedFund("119771", "Parag Parikh Flexi Cap", "INF001")]

    facts = await S.build_mutual_fund_query_facts(None, resolved, "comparison")

    assert [p.fund_name for p in facts.peers] == ["HDFC Flexi Cap"]
    assert facts.peers[0].return_3y_cagr_pct == 15.0
    assert facts.peers[0].shortlist_rank == 2


@pytest.mark.asyncio
async def test_two_named_funds_full_facts_no_auto_peers(monkeypatch):
    monkeypatch.setattr(S, "ranking_by_isin",
                        lambda isin: _row(isin, "Fund " + isin))
    resolved = [
        ResolvedFund("1", "A", "INF001"),
        ResolvedFund("2", "B", "INF002"),
    ]

    facts = await S.build_mutual_fund_query_facts(None, resolved, "comparison")

    assert len(facts.funds) == 2
    assert all(f.has_house_view for f in facts.funds)
    assert facts.peers == []


@pytest.mark.asyncio
async def test_returns_intent_has_no_peers(monkeypatch):
    monkeypatch.setattr(S, "ranking_by_isin", lambda isin: _row("INF001", "Parag Parikh Flexi Cap"))
    resolved = [ResolvedFund("119771", "Parag Parikh Flexi Cap", "INF001")]

    facts = await S.build_mutual_fund_query_facts(None, resolved, "returns")

    assert facts.peers == []
