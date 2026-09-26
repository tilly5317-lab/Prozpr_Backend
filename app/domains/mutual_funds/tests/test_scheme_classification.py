"""Unit tests for the canonical classifier's response-builder helper.

``fill_classification`` is the function API response builders call to honor the
rule: trust the classification the data already carries; compute the rest from
``scheme_classification`` rather than returning a blank to the client.
"""

import pytest

from app.domains.mutual_funds.services.scheme_classification import (
    ASSET_CLASS_LOOKTHROUGH_WEIGHTS,
    add_to_asset_class_mix,
    asset_class_lookthrough,
    classify_holding,
    fill_classification,
)


# ---------------------------------------------------------------------------
# Domestic FoF look-through — a FoF wraps a single underlying scheme, so it must
# classify by that scheme's asset class, not fall to a blanket "Others".
# ---------------------------------------------------------------------------


def test_domestic_fof_equity_index_looks_through_to_equity():
    # Real case (Sourabh's portfolio): an equity-index FoF was mis-bucketed as
    # "Others". The underlying is a sectoral/thematic equity index.
    assert classify_holding("FoF Domestic", "Groww Nifty India Internet ETF FOF") == (
        "Equity",
        "sector_equities",
    )


def test_domestic_fof_debt_index_looks_through_to_debt():
    # Safety: a debt-index FoF's name also contains "Nifty" — debt detection must
    # run first so it reads as Debt, not equity.
    assert classify_holding("FoF Domestic", "Nippon India Nifty G-Sec ETF FoF") == (
        "Debt",
        "debt_subgroup",
    )


def test_domestic_fof_gold_stays_commodities():
    assert classify_holding("FoF Domestic", "ABC Gold Savings FoF") == (
        "Others",
        "gold_commodities",
    )


def test_domestic_fof_ambiguous_keeps_conservative_others():
    # A name matching no known underlying pattern keeps the conservative bucket.
    assert classify_holding("FoF Domestic", "ABC Domestic FoF") == (
        "Others",
        "others_fofs",
    )


def test_fills_from_sub_category_when_nothing_supplied():
    assert fill_classification("Liquid Fund", "Whatever Liquid Fund") == ("Debt", "near_debt")
    assert fill_classification("Large Cap Fund", "X Large Cap") == ("Equity", "low_beta_equities")
    assert fill_classification("Gold ETF", "X Gold ETF") == ("Others", "gold_commodities")


def test_trusts_supplied_values_even_when_they_differ_from_the_classifier():
    # A curated MfFundRating value wins — the classifier only fills blanks.
    assert fill_classification(
        "Liquid Fund", "X", asset_class="Equity", asset_subgroup="custom_subgroup"
    ) == ("Equity", "custom_subgroup")


def test_fills_only_the_missing_field():
    # asset_class supplied, subgroup blank → subgroup derived, class kept as given.
    assert fill_classification("Liquid Fund", "X", asset_class="Debt") == ("Debt", "near_debt")


def test_name_based_last_resort_when_sub_category_is_missing():
    # No usable sub_category → classify_holding yields nothing → resolve_asset_bucket from name.
    assert fill_classification(None, "HDFC Liquid Fund") == ("Debt", None)
    assert fill_classification("Totally Unknown Category", "Reliance Pharma Fund") == ("Equity", None)


def test_defaults_to_others_when_nothing_is_classifiable():
    assert fill_classification(None, None) == ("Others", None)


# ---------------------------------------------------------------------------
# Multi-asset / hybrid look-through
# ---------------------------------------------------------------------------


def test_lookthrough_weights_each_row_sums_to_one():
    for sub_category, weights in ASSET_CLASS_LOOKTHROUGH_WEIGHTS.items():
        assert round(sum(weights.values()), 6) == 1.0, sub_category


def test_lookthrough_matches_canonical_and_raw_sub_category():
    # Canonical key.
    assert asset_class_lookthrough("Multi-Asset Allocation Fund") == {
        "Equity": 0.725,
        "Debt": 0.125,
        "Others": 0.15,
    }
    # Raw SEBI/CSV forms normalize to the same canonical key.
    assert asset_class_lookthrough("Multi Asset Allocation") == {
        "Equity": 0.725,
        "Debt": 0.125,
        "Others": 0.15,
    }
    assert asset_class_lookthrough("Dynamic Asset Allocation or Balanced Advantage") == {
        "Equity": 0.50,
        "Debt": 0.50,
    }
    assert asset_class_lookthrough("Aggressive Hybrid Fund") == {
        "Equity": 0.725,
        "Debt": 0.175,
        "Others": 0.10,
    }
    assert asset_class_lookthrough("Conservative Hybrid Fund") == {"Equity": 0.175, "Debt": 0.825}


def test_lookthrough_returns_none_for_single_class_funds():
    assert asset_class_lookthrough("Large Cap Fund") is None
    assert asset_class_lookthrough("Liquid Fund") is None
    # Flexi Cap is a PURE-EQUITY SEBI category. It briefly carried a
    # 0.725/0.175/0.10 band on the reasoning that its schemes hold incidental
    # cash/debt; that manufactured ~3% debt on all-equity portfolios and made
    # chat and the Invest page disagree about identical holdings. Only genuinely
    # blended categories belong in the table.
    assert asset_class_lookthrough("Flexi Cap Fund") is None
    assert asset_class_lookthrough(None) is None
    assert asset_class_lookthrough("") is None
    assert asset_class_lookthrough("   ") is None


def test_add_to_asset_class_mix_splits_blended_fund():
    mix: dict[str, float] = {}
    add_to_asset_class_mix(
        mix, amount=100.0, sub_category="Multi-Asset Allocation Fund", fallback_asset_class="Equity"
    )
    assert mix == pytest.approx({"Equity": 72.5, "Debt": 12.5, "Others": 15.0})


def test_add_to_asset_class_mix_debt_heavy_hybrid():
    mix: dict[str, float] = {}
    add_to_asset_class_mix(
        mix, amount=100.0, sub_category="Conservative Hybrid Fund", fallback_asset_class="Equity"
    )
    # Falls 82.5% to Debt despite the engine's single-class fallback being Equity-ish;
    # look-through wins over the fallback.
    assert mix == pytest.approx({"Equity": 17.5, "Debt": 82.5})


def test_add_to_asset_class_mix_uses_fallback_for_pure_funds():
    mix: dict[str, float] = {}
    add_to_asset_class_mix(
        mix, amount=100.0, sub_category="Large Cap Fund", fallback_asset_class="Equity"
    )
    add_to_asset_class_mix(
        mix, amount=40.0, sub_category="Liquid Fund", fallback_asset_class="Debt"
    )
    assert mix == pytest.approx({"Equity": 100.0, "Debt": 40.0})


def test_add_to_asset_class_mix_accumulates_and_conserves_total():
    mix: dict[str, float] = {}
    add_to_asset_class_mix(
        mix, amount=100.0, sub_category="Large Cap Fund", fallback_asset_class="Equity"
    )
    add_to_asset_class_mix(
        mix, amount=200.0, sub_category="Multi-Asset Allocation Fund", fallback_asset_class="Equity"
    )
    # Equity: 100 + 0.725*200 = 245; Debt: 0.125*200 = 25; Others: 0.15*200 = 30. Total 300.
    assert mix == pytest.approx({"Equity": 245.0, "Debt": 25.0, "Others": 30.0})
    assert sum(mix.values()) == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# AMFI-spelled raw labels — the second vocabulary ``mf_fund_metadata.sub_category``
# carries next to the SEBI "Duration" names. Each must land on the same canonical
# bucket as its SEBI twin, or the holding counts as unknown value in every rollup.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sub_category", "scheme_name", "expected"),
    [
        # Real dev-smoke holding: fell through to (None, None).
        ("Short Term Fund", "HDFC Short Term Fund - Regular Plan - Growth", ("Debt", "short_debt")),
        ("Ultra Short Term Fund", "DSP Ultra Short Term Fund - Regular Plan - IDCW Payout", ("Debt", "near_debt")),
        ("Ultra Short to Short Term Fund", "Kotak Ultra Short to Short Term Fund - Direct Plan", ("Debt", "short_debt")),
        ("Medium Term Fund", "DSP Medium Term Fund - Regular Plan - Monthly IDCW", ("Debt", "medium_debt")),
        ("Medium to Long Term Fund", "HDFC Medium to Long Term Fund - Regular Plan - Growth Option", ("Debt", "medium_debt")),
        ("Long Term Fund", "UTI Long Term Fund - Direct Plan - Growth", ("Debt", "long_duration_debt")),
        ("10-year Constant Maturity Gilt Fund", "UTI 10 year Constant Maturity Gilt Fund - Direct Plan - Growth", ("Debt", "long_duration_debt")),
        ("ELSS- Tax Saver Fund", "Mirae Asset ELSS Tax Saver Fund - Regular Plan - Growth", ("Equity", "tax_efficient_equities")),
        ("Balanced Advantage Fund/ Dynamic Asset Allocation", "UTI Balanced Advantage Fund - Direct Plan - Growth", ("Equity", "medium_beta_equities")),
        ("Multi Asset Allocation Fund", "UTI Multi Asset Allocation Fund - Direct Plan - IDCW", ("Equity", "medium_beta_equities")),
        ("Silver ETF", "Mirae Asset Silver ETF", ("Others", "silver_commodities")),
        ("Debt ETF", "UTI Nifty 10 yr Benchmark G-Sec ETF - Direct Plan - Growth", ("Debt", "debt_subgroup")),
    ],
)
def test_term_style_raw_labels_classify_like_their_sebi_twins(sub_category, scheme_name, expected):
    assert classify_holding(sub_category, scheme_name) == expected


@pytest.mark.parametrize(
    ("sub_category", "scheme_name", "expected"),
    [
        # Index/ETF spellings dispatch by name exactly like "Index Funds" / "Other  ETFs".
        ("Equity ETF", "Aditya Birla Sun Life Nifty 50 ETF", ("Equity", "low_beta_equities")),
        ("Equity ETF", "Mirae Asset Nifty EV and New Age Automotive ETF", ("Equity", "sector_equities")),
        ("Other ETF", "Groww Nifty 50 ETF", ("Equity", "low_beta_equities")),
        ("Other ETF", "Groww BSE Power ETF", ("Equity", "sector_equities")),
        # FoF spellings look through to the wrapped scheme like "FoF Domestic" / "FoF Overseas".
        ("Fund of Funds Scheme (Domestic)", "HSBC Gold ETF Fund of Fund - Regular Plan - Growth", ("Others", "gold_commodities")),
        ("Fund of Funds Scheme (Domestic)", "Groww Nifty India Defence ETF FOF - Regular Plan", ("Equity", "sector_equities")),
        ("Fund of Funds investing overseas", "Axis Greater China Equity Fund of Fund - Regular Plan - IDCW Option", ("Equity", "china_equities")),
        ("Fund of Funds investing overseas", "HSBC Global Emerging Markets Fund - Direct Plan - Growth", ("Others", "others_fofs")),
    ],
)
def test_term_style_index_etf_and_fof_labels_dispatch_by_name(sub_category, scheme_name, expected):
    assert classify_holding(sub_category, scheme_name) == expected


def test_lookthrough_accepts_term_style_hybrid_labels():
    # The same raw labels must also split in the asset-class rollup, or the
    # donut and chat count a balanced-advantage fund as 100% equity.
    assert asset_class_lookthrough("Balanced Advantage Fund/ Dynamic Asset Allocation") == {
        "Equity": 0.50,
        "Debt": 0.50,
    }
    assert asset_class_lookthrough("Multi Asset Allocation Fund") == {
        "Equity": 0.725,
        "Debt": 0.125,
        "Others": 0.15,
    }
