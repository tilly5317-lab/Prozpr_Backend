"""Tier 2: returns metrics from NAV history.

Computes:
  - 1Y, 3Y, 5Y, 7Y CAGR
  - 3Y rolling returns anchored at end of year 2020..2026
  - Sharpe (3Y), Beta, Tracking Error vs benchmark (annualized)
  - Benchmark 3Y rolling returns for the same anchors

NAV dates from mfapi.in are 'DD-MM-YYYY' strings.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .benchmarks import benchmark_for, fixed_rate, hybrid_blend
from .nav_fetch import fetch_nav

ROLL_YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
RISK_FREE_PCT = 6.5  # for Sharpe denominator (annualized risk-free rate)


def nav_to_series(nav_data: list[dict]) -> pd.Series:
    df = pd.DataFrame(nav_data)
    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y")
    df["nav"] = df["nav"].astype(float)
    df = df.dropna().sort_values("date").drop_duplicates("date")
    return df.set_index("date")["nav"]


def _nearest_on_or_before(series: pd.Series, target: pd.Timestamp) -> float | None:
    s = series[series.index <= target]
    if s.empty:
        return None
    return float(s.iloc[-1])


def cagr(series: pd.Series, years: float, anchor: pd.Timestamp | None = None) -> float | None:
    if anchor is None:
        anchor = series.index.max()
    start = anchor - pd.DateOffset(years=int(round(years * 12)) // 12, months=int(round(years * 12)) % 12)
    end_v = _nearest_on_or_before(series, anchor)
    start_v = _nearest_on_or_before(series, start)
    if not (end_v and start_v) or start_v <= 0:
        return None
    # Verify we actually have ~years of history (allow 30-day grace)
    earliest = series.index.min()
    if (start - earliest).days < -30:
        return None
    return float((end_v / start_v) ** (1 / years) - 1) * 100


def cagr_anchored_endyear(series: pd.Series, anchor_year: int, lookback_years: int = 3) -> float | None:
    anchor = pd.Timestamp(year=anchor_year, month=12, day=31)
    if anchor > series.index.max():
        anchor = series.index.max()
    if anchor.year < anchor_year:
        return None
    start = anchor - pd.DateOffset(years=lookback_years)
    end_v = _nearest_on_or_before(series, anchor)
    start_v = _nearest_on_or_before(series, start)
    if not (end_v and start_v) or start_v <= 0:
        return None
    earliest = series.index.min()
    if (start - earliest).days < -30:
        return None
    return float((end_v / start_v) ** (1 / lookback_years) - 1) * 100


def daily_log_returns(series: pd.Series) -> pd.Series:
    """Log returns on the native NAV frequency (no resampling)."""
    return np.log(series / series.shift(1)).dropna()


def _periods_per_year(s: pd.Series) -> float:
    if len(s) < 2:
        return 252.0
    span_days = (s.index.max() - s.index.min()).days
    if span_days <= 0:
        return 252.0
    return len(s) * 365.25 / span_days


def sharpe_3y(series: pd.Series, rf_pct: float = RISK_FREE_PCT) -> float | None:
    """Sharpe = (CAGR_3Y - rf) / annualized_vol_3Y. Robust to publish frequency."""
    end = series.index.max()
    start = end - pd.DateOffset(years=3)
    if (start - series.index.min()).days < -30:
        return None
    sub = series.loc[start:end]
    r = daily_log_returns(sub)
    if len(r) < 100:
        return None
    ppy = _periods_per_year(sub)
    ann_vol = r.std() * math.sqrt(ppy) * 100
    cg = cagr(series, 3)
    if cg is None or ann_vol <= 0:
        return None
    return (cg - rf_pct) / ann_vol


def beta_te(fund: pd.Series, bench: pd.Series, years: int = 3) -> tuple[float | None, float | None]:
    """Return (beta, tracking_error_annualized_pct). None for both when bench variance ~ 0."""
    end = min(fund.index.max(), bench.index.max())
    start = end - pd.DateOffset(years=years)
    if (start - max(fund.index.min(), bench.index.min())).days < -30:
        return None, None
    f_sub = fund.loc[start:end]
    b_sub = bench.loc[start:end]
    f = daily_log_returns(f_sub)
    b = daily_log_returns(b_sub)
    df = pd.concat([f, b], axis=1, join="inner").dropna()
    df.columns = ["f", "b"]
    if len(df) < 100:
        return None, None
    var_b = df["b"].var()
    if var_b < 1e-10:  # fixed-rate / synthetic benchmark
        return None, None
    cov = df.cov().iloc[0, 1]
    beta = cov / var_b
    ppy = _periods_per_year(f_sub)
    te = (df["f"] - df["b"]).std() * math.sqrt(ppy) * 100
    return float(beta), float(te)


def synthetic_fixed_series(start: pd.Timestamp, end: pd.Timestamp, rate_pct: float) -> pd.Series:
    idx = pd.date_range(start, end, freq="B")
    daily = (1 + rate_pct / 100) ** (1 / 252)
    vals = daily ** np.arange(len(idx))
    return pd.Series(vals * 100, index=idx)


def synthetic_blend_series(equity: pd.Series, equity_w: float, rate_pct: float) -> pd.Series:
    """Daily-rebalanced blend of equity NAV and a fixed rate."""
    end = equity.index.max()
    start = equity.index.min()
    fixed = synthetic_fixed_series(start, end, rate_pct).reindex(equity.index, method="ffill")
    eq_norm = equity / equity.iloc[0]
    fx_norm = fixed / fixed.iloc[0]
    blended = equity_w * eq_norm + (1 - equity_w) * fx_norm
    return blended * 100


def get_benchmark_series(token: str | int, fund_series: pd.Series) -> pd.Series | None:
    if isinstance(token, int):
        j = fetch_nav(token)
        if not j:
            return None
        return nav_to_series(j["data"])
    if isinstance(token, str) and token.startswith("FIXED_"):
        return synthetic_fixed_series(fund_series.index.min(), fund_series.index.max(), fixed_rate(token))
    if isinstance(token, str) and token.startswith("BLEND_"):
        eq, rate = hybrid_blend(token)
        from .benchmarks import NIFTY_50
        eqj = fetch_nav(NIFTY_50)
        if not eqj:
            return None
        eq_series = nav_to_series(eqj["data"])
        return synthetic_blend_series(eq_series, eq, rate)
    return None


def compute(scheme_code: int, sub_category: str, scheme_name: str = "") -> dict:
    out = {}
    j = fetch_nav(scheme_code)
    if not j or not j.get("data"):
        return {"_error": "no NAV data"}
    fund = nav_to_series(j["data"])
    out["nav_start"] = fund.index.min().strftime("%Y-%m-%d")
    out["nav_end"] = fund.index.max().strftime("%Y-%m-%d")
    out["nav_points"] = len(fund)

    out["1Y CAGR"] = cagr(fund, 1)
    out["3Y CAGR"] = cagr(fund, 3)
    out["5Y CAGR"] = cagr(fund, 5)
    out["7Y CAGR"] = cagr(fund, 7)

    for y in ROLL_YEARS:
        out[f"3Y rolling {y}"] = cagr_anchored_endyear(fund, y, 3)

    out["Sharpe (3Y)"] = sharpe_3y(fund)

    bench_token = benchmark_for(sub_category, scheme_name)
    out["benchmark_token"] = str(bench_token)
    bench = get_benchmark_series(bench_token, fund)
    if bench is not None:
        b, te = beta_te(fund, bench, years=3)
        out["Beta"] = b
        out["Tracking Error"] = te
        for y in ROLL_YEARS:
            out[f"Bench 3Y rolling {y}"] = cagr_anchored_endyear(bench, y, 3)
    else:
        out["Beta"] = None
        out["Tracking Error"] = None
        for y in ROLL_YEARS:
            out[f"Bench 3Y rolling {y}"] = None
    return out


if __name__ == "__main__":
    # Smoke test on the 10 pilot funds
    pilots = [
        (119018, "Large Cap Fund", "HDFC Large Cap Fund - Direct Growth"),
        (122639, "Flexi Cap Fund", "Parag Parikh Flexi Cap Fund - Direct Growth"),
        (119091, "Liquid Fund", "HDFC Liquid Fund - Direct Growth"),
    ]
    for code, sub, name in pilots:
        print(f"\n=== {code} {name} ===")
        r = compute(code, sub, name)
        for k, v in r.items():
            if isinstance(v, float):
                print(f"  {k}: {v:.2f}")
            else:
                print(f"  {k}: {v}")
