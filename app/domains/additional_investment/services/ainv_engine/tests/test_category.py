"""Pure tests for the category helper (fakes only — the ranking loader is
monkeypatched with in-memory rows; no CSV, no DB, no LLM)."""

from dataclasses import dataclass

import pytest

from app.domains.additional_investment.services.ainv_engine import category as cat


@dataclass
class _Row:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    fund_name: str


_RANKING = {
    "high_beta_equities": [
        _Row("high_beta_equities", "Mid Cap Fund", 1, "INF_M1", "M1", "Mid One"),
        _Row("high_beta_equities", "Small Cap Fund", 2, "INF_S1", "S1", "Small One"),
        _Row("high_beta_equities", "Small Cap Fund", 3, "INF_S2", "S2", "Small Two"),
        _Row("high_beta_equities", "Small Cap Fund", 4, "INF_S3", "S3", "Small Three"),
    ],
    "tax_efficient_equities": [
        _Row("tax_efficient_equities", "ELSS", 1, "INF_E1", "E1", "Tax Saver One"),
    ],
    "multi_asset": [
        _Row("multi_asset", "Multi Asset Allocation", 1, "INF_A1", "A1", "Multi One"),
    ],
}


@pytest.fixture(autouse=True)
def _patch_ranking(monkeypatch):
    monkeypatch.setattr(cat, "get_fund_ranking", lambda: _RANKING)


def test_resolve_direct_and_synonyms():
    assert cat.resolve_category("Small Cap Fund") == "Small Cap Fund"
    assert cat.resolve_category("smallcap") == "Small Cap Fund"
    assert cat.resolve_category("small cap funds") == "Small Cap Fund"
    assert cat.resolve_category("midcap") == "Mid Cap Fund"
    assert cat.resolve_category("elss") == "ELSS"
    assert cat.resolve_category("tax saving") == "ELSS"


def test_resolve_unknown_and_none():
    assert cat.resolve_category("REIT funds") is None
    assert cat.resolve_category(None) is None
    assert cat.resolve_category("   ") is None


def test_top_funds_ordered_by_rank_and_capped():
    funds = cat.top_funds_for_category("Small Cap Fund", n=2)
    assert [f.fund_name for f in funds] == ["Small One", "Small Two"]
    assert len(cat.top_funds_for_category("Small Cap Fund")) == 3


def test_category_subgroup_is_top_ranked_funds_subgroup():
    assert cat.category_subgroup("Small Cap Fund") == "high_beta_equities"
    assert cat.category_subgroup("Unknown Cat") is None


def _buy(sub_category, asset_subgroup, amount=10000.0):
    """Stand-in for engine FundBuy (only the attrs category_status reads)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        sub_category=sub_category, asset_subgroup=asset_subgroup, amount_inr=amount
    )


_EXCLUDE = {"tax_efficient_equities", "non_mf_equities"}


def test_status_precedence_not_ranked_first():
    assert cat.category_status(
        None, deficit_facts=[], buys=[], exclude_subgroups=_EXCLUDE
    ) == "not_ranked"


def test_status_excluded_by_policy():
    assert cat.category_status(
        "ELSS", deficit_facts=[], buys=[], exclude_subgroups=_EXCLUDE
    ) == "excluded_by_policy"


def test_status_in_plan_when_a_buy_matches_category():
    buys = [_buy("Small Cap Fund", "high_beta_equities")]
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=[], buys=buys, exclude_subgroups=_EXCLUDE
    ) == "in_plan"


def test_status_subgroup_funded_via_other_funds():
    buys = [_buy("Mid Cap Fund", "high_beta_equities")]
    facts = [{"subgroup": "high_beta_equities", "ideal_inr": 100000.0,
              "current_inr": 40000.0, "gap_inr": 60000.0, "buy_inr": 10000.0}]
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=facts, buys=buys, exclude_subgroups=_EXCLUDE
    ) == "subgroup_funded_other_funds"


def test_status_at_or_above_ideal_when_no_gap_row():
    # subgroup absent from deficit rows == no gap == nothing deployed there
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=[], buys=[], exclude_subgroups=_EXCLUDE
    ) == "at_or_above_ideal"


def test_status_sip_degrades_to_plan_by_goals():
    # deficit_facts=None (SIP / legacy path) and no matching buy
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=None, buys=[], exclude_subgroups=_EXCLUDE
    ) == "plan_by_goals"


def test_resolve_rejects_substring_collisions():
    # word-boundary matching: category words inside OTHER words must not match
    assert cat.resolve_category("Goldman Sachs fund") is None
    assert cat.resolve_category("what about my contract value") is None
    assert cat.resolve_category("focused fund") is None


def test_resolve_still_matches_whole_words_and_phrases():
    assert cat.resolve_category("midcap funds please") == "Mid Cap Fund"
    assert cat.resolve_category("small cap funds only") == "Small Cap Fund"


def test_status_unfilled_gap_degrades_to_plan_by_goals():
    # gap > 0 but nothing placed (rounding/caps/scarcity): never claim
    # "at/above ideal" (false) nor "funded" (false) — generic goals narration.
    facts = [{"subgroup": "high_beta_equities", "ideal_inr": 100000.0,
              "current_inr": 99950.0, "gap_inr": 50.0, "buy_inr": 0.0}]
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=facts, buys=[], exclude_subgroups=_EXCLUDE
    ) == "plan_by_goals"


def test_status_zero_gap_row_is_at_or_above_ideal():
    facts = [{"subgroup": "high_beta_equities", "ideal_inr": 100000.0,
              "current_inr": 120000.0, "gap_inr": 0.0, "buy_inr": 0.0}]
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=facts, buys=[], exclude_subgroups=_EXCLUDE
    ) == "at_or_above_ideal"
