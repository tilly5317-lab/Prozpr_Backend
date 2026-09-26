"""Pure aggregation tests for the holdings snapshot (fakes only, no DB —
house style; the async loader is a thin query wrapper exercised in service
tests via monkeypatch)."""

import pytest

from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    aggregate_holdings,
)


def test_classifies_mf_rows_to_subgroups_and_sums():
    rows = [
        # (instrument_type, current_value, sub_category, scheme_name)
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
        ("mutual_fund", 50000.0, "Large Cap Fund", "Beta Large Cap"),
        ("mutual_fund", 30000.0, "Small Cap Fund", "Gamma Small Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.by_subgroup["low_beta_equities"] == pytest.approx(150000.0)
    assert snap.by_subgroup["high_beta_equities"] == pytest.approx(30000.0)
    assert snap.unknown_inr == 0.0
    assert snap.total_inr == pytest.approx(180000.0)


def test_direct_stocks_bucket_to_non_mf_equities_not_unknown():
    rows = [
        ("equity", 200000.0, None, "RELIANCE"),
        ("stock", 50000.0, None, "TCS"),
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.non_mf_equity_inr == pytest.approx(250000.0)
    assert snap.unknown_inr == 0.0


def test_elss_lands_in_frozen_property():
    rows = [("mutual_fund", 60000.0, "ELSS Tax Saver Fund", "Tax Saver X")]
    snap = aggregate_holdings(rows)
    assert snap.elss_inr == pytest.approx(60000.0)


def test_unclassifiable_counts_in_total_but_not_map():
    rows = [
        ("mutual_fund", 40000.0, None, "Mystery Scheme 42"),
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.unknown_inr == pytest.approx(40000.0)
    assert "unknown" not in snap.by_subgroup
    assert snap.total_inr == pytest.approx(140000.0)


def test_zero_and_negative_values_skipped():
    snap = aggregate_holdings([
        ("mutual_fund", 0.0, "Large Cap Fund", "Alpha"),
        ("mutual_fund", -5.0, "Large Cap Fund", "Beta"),
    ])
    assert snap.total_inr == 0.0
    assert snap.by_subgroup == {}
