"""Unit tests for preference normalization (pure functions, no I/O)."""

import pytest

from app.domains.mutual_funds.services.investment_preferences import (
    DEFAULT_TILT_STEP_PP,
    normalize_tilt,
)

MIX = {"equity": 55.0, "debt": 35.0, "others": 10.0}


def test_delta_tilt_renormalizes_other_classes_pro_rata():
    r = normalize_tilt(MIX, tilt_asset_class="equity",
                       tilt_delta_pp=10.0, tilt_target_pct=None)
    assert r.mix_pct["equity"] == pytest.approx(65.0)
    assert r.mix_pct["debt"] == pytest.approx(27.22, abs=0.01)
    assert sum(r.mix_pct.values()) == pytest.approx(100.0)


def test_absolute_tilt_take_equity_to_70():
    r = normalize_tilt(MIX, tilt_asset_class="equity",
                       tilt_delta_pp=None, tilt_target_pct=70.0)
    assert r.mix_pct["equity"] == pytest.approx(70.0)
    assert sum(r.mix_pct.values()) == pytest.approx(100.0)


def test_tilt_clamps_at_100():
    r = normalize_tilt(MIX, tilt_asset_class="equity",
                       tilt_delta_pp=60.0, tilt_target_pct=None)
    assert r.mix_pct["equity"] == pytest.approx(100.0)
    assert sum(r.mix_pct.values()) == pytest.approx(100.0)


def test_no_number_equity_tilt_adds_a_step_more_equity():
    # "more equity" with no number → recommended equity + DEFAULT_TILT_STEP_PP,
    # so it always increases (never a step backward).
    r = normalize_tilt(MIX, tilt_asset_class="equity",
                       tilt_delta_pp=None, tilt_target_pct=None)
    assert r.default_step_applied
    assert r.mix_pct["equity"] == pytest.approx(55.0 + DEFAULT_TILT_STEP_PP)
    assert r.mix_pct["equity"] > MIX["equity"]              # more means more


def test_no_number_safer_tilt_adds_a_step_more_debt():
    # "make it safer" → debt + step.
    r = normalize_tilt(MIX, tilt_asset_class="debt",
                       tilt_delta_pp=None, tilt_target_pct=None)
    assert r.mix_pct["debt"] == pytest.approx(35.0 + DEFAULT_TILT_STEP_PP)
    assert r.mix_pct["equity"] < MIX["equity"]              # safer = less equity
