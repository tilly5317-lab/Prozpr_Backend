"""Unit tests for benchmarks domain pure logic (scraper parse, date math, cadence).

DB-backed paths (pg upsert / reads) are exercised in integration, not here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domains.benchmarks.services import niftyindices_fetcher as nf
from app.domains.benchmarks.services.benchmark_data_service import (
    BENCHMARK_LOCK_KEY,
    DEFAULT_LOOKBACK_DAYS,
    KNOWN_INDICES,
    NIFTY_50_CODE,
    _years_ago,
)
from app.domains.benchmarks.services.benchmark_scheduler import BENCHMARK_REFRESH_HOURS


# ── scraper parsing ─────────────────────────────────────────────────────────


def test_parse_record_valid_with_ntr():
    row = nf._parse_record(
        {"Date": "30 Jun 2021", "TotalReturnsIndex": "24000.50", "NTR_Value": "23000.10"}
    )
    assert row is not None
    assert row.tri_date == date(2021, 6, 30)
    assert row.tri_value == Decimal("24000.50")
    assert row.ntr_value == Decimal("23000.10")


def test_parse_record_missing_ntr_is_none():
    row = nf._parse_record({"Date": "01 Apr 2022", "TotalReturnsIndex": "100"})
    assert row is not None and row.ntr_value is None


def test_parse_record_malformed_returns_none():
    assert nf._parse_record({"Date": "not-a-date", "TotalReturnsIndex": "1"}) is None
    assert nf._parse_record({"TotalReturnsIndex": "1"}) is None  # no Date key


def test_fmt_matches_niftyindices_format():
    assert nf._fmt(date(2026, 6, 4)) == "04-Jun-2026"


# ── date windowing ──────────────────────────────────────────────────────────


def test_iter_date_windows_contiguous_and_bounded():
    wins = list(nf.iter_date_windows(date(2020, 1, 1), date(2024, 1, 1), window_years=2))
    assert wins[0][0] == date(2020, 1, 1)
    assert wins[-1][1] == date(2024, 1, 1)
    for (_s1, e1), (s2, _e2) in zip(wins, wins[1:]):
        assert (s2 - e1).days == 1  # contiguous, non-overlapping


def test_iter_date_windows_single_window_when_short():
    wins = list(nf.iter_date_windows(date(2023, 1, 1), date(2023, 6, 1), window_years=2))
    assert wins == [(date(2023, 1, 1), date(2023, 6, 1))]


# ── data-service date math + constants ──────────────────────────────────────


def test_years_ago_simple():
    assert _years_ago(5, date(2026, 6, 17)) == date(2021, 6, 17)


def test_years_ago_leap_day_falls_back_to_28():
    assert _years_ago(1, date(2024, 2, 29)) == date(2023, 2, 28)


def test_nifty50_is_a_known_index_with_metadata():
    assert NIFTY_50_CODE == "NIFTY 50"
    meta = KNOWN_INDICES[NIFTY_50_CODE]
    assert meta["asset_class"] == "EQUITY"
    assert meta["earliest_available"] == nf.NIFTY_TRI_EARLIEST


def test_constants_are_sane():
    assert BENCHMARK_LOCK_KEY == 7421101
    assert DEFAULT_LOOKBACK_DAYS >= 1


# ── scheduler cadence invariant: "tri" = thrice daily ───────────────────────


def test_scheduler_fires_three_times_a_day():
    assert len(BENCHMARK_REFRESH_HOURS) == 3
    assert all(0 <= h <= 23 for h in BENCHMARK_REFRESH_HOURS)
    assert len(set(BENCHMARK_REFRESH_HOURS)) == 3  # distinct hours
