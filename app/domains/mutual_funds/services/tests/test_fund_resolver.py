"""Name → ranking-row resolution against a FIXTURE CSV (never the live one)."""

from pathlib import Path

import pytest

import app.domains.rebalancing.services.rebal_engine.fund_rank as fund_rank
from app.domains.mutual_funds.services.fund_ranking_lookup import resolve_ranked_fund

_FIXTURE = Path(__file__).parent / "fund_ranking_fixture.csv"

_CACHED = ("get_fund_ranking", "get_all_rows", "get_rejection_reasons",
           "get_force_exit_isins")


def _clear_caches():
    for fn in _CACHED:
        cached = getattr(fund_rank, fn, None)
        if cached is not None and hasattr(cached, "cache_clear"):
            cached.cache_clear()


@pytest.fixture(autouse=True)
def _fixture_csv(monkeypatch):
    monkeypatch.setattr(fund_rank, "_CSV_PATH", _FIXTURE)
    _clear_caches()
    yield
    _clear_caches()


def test_recommended_fund_resolves_with_isin_and_category():
    r = resolve_ranked_fund("alpha large cap")
    assert r.status == "recommended"
    assert r.isin == "INFTEST00001" and r.sub_category == "Large Cap Fund"


def test_rejected_fund_carries_rejection_text():
    r = resolve_ranked_fund("beta mid cap")
    assert r.status == "rejected"
    assert "tenure" in (r.rejection_text or "")


def test_unknown_fund_is_unknown():
    assert resolve_ranked_fund("definitely not a real scheme 123").status == "unknown"


def test_stopword_only_query_is_not_guessed():
    assert resolve_ranked_fund("fund").status in ("ambiguous", "unknown")


def test_two_matches_are_ambiguous_never_guessed():
    r = resolve_ranked_fund("large cap")           # Alpha + Gamma: different funds
    assert r.status == "ambiguous"
    assert len(r.candidates) == 2


def test_token_subset_matches_omitted_middle_word():
    # "Kappa Bluechip" omits "Prudential" — contiguous substring would miss it;
    # token-subset matches because every typed word is present.
    r = resolve_ranked_fund("Kappa Bluechip")
    assert r.status == "recommended"
    assert "Kappa" in (r.fund_name or "")


def test_bonus_variant_collapses_not_ambiguous():
    # Growth and Bonus variants of the same fund → one fund, not ambiguous.
    r = resolve_ranked_fund("Kappa Prudential Bluechip")
    assert r.status == "recommended"


def test_plan_option_variants_collapse_to_one_direct_growth():
    # Four? No — two Zeta variants (Direct/Growth, Regular/IDCW) are ONE fund.
    r = resolve_ranked_fund("zeta flexi cap")
    assert r.status == "rejected"                  # not ambiguous
    assert "direct" in (r.fund_name or "").lower()
    assert "growth" in (r.fund_name or "").lower()
    assert "too new" in (r.rejection_text or "")
