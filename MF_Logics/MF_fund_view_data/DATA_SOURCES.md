# MF_fund_view_data — Data sources

Where every field in the MF evaluation output comes from. Use this when wiring the pipeline into a quarterly job, or when answering "where did this number come from?"

## External sources (network)

| Source | URL / endpoint | What we pull | Used in |
|---|---|---|---|
| **mfapi.in** (community-maintained free NAV API) | `GET https://api.mfapi.in/mf/{schemeCode}` → JSON with `data: [{date, nav}, …]` and `meta` | Full NAV history (daily) per AMFI scheme code | [src/nav_fetch.py](src/nav_fetch.py) |
| **Groww — search endpoint** (undocumented JSON, used by their web app) | `GET https://groww.in/v1/api/search/v3/query/global/st_query?from=0&size=5&query={cleaned_name}` | Resolves a fund name → Groww `search_id` slug | [src/tier3_groww.py](src/tier3_groww.py) `search_slug()` |
| **Groww — scheme detail endpoint** (undocumented JSON) | `GET https://groww.in/v1/api/data/mf/web/v2/scheme/search/{slug}` | AUM, expense ratio, exit load, min investment, fund-manager history, holdings, portfolio turnover, benchmark name, category rank, ISIN | [src/tier3_groww.py](src/tier3_groww.py) `fetch_detail_by_slug()` |

Both Groww endpoints are public JSON used by groww.in itself — no auth, no Cloudflare wall as of the original work. They are unofficial and may change without notice; we send a desktop-Chrome `User-Agent` header and sleep 0.4 s between calls. mfapi.in is community-run; treat both as best-effort and cache aggressively (we do — see "Caching" below).

## Local inputs (files in the repo)

| File | What's in it | Notes |
|---|---|---|
| `latest_nav_active.csv` | Universe of active MF schemes with `schemeCode`, `schemeName`, `isinGrowth`, `isinDivReinvestment`, `sub_category`, `fundHouse` | Snapshot — refresh per quarter from AMFI/your existing extraction pipeline. This is the only seed input; everything else is fetched or derived from these rows. Both ISIN columns are preserved downstream (`isin_growth`, `isin_div_reinvest`) because customer CAS holdings can be in either. |
| `MF_evaluation framework.xlsx` | Column layout / section grouping for the output workbook | Reference only; the actual column order is hardcoded in [src/build_xlsx.py](src/build_xlsx.py) `SECTION_GROUPS`. |

## Derived locally (no network)

| Logic | Inputs | Outputs | File |
|---|---|---|---|
| Tier 1 — classification | `schemeName`, `sub_category` from the CSV | `asset_class`, `plan_class` (Direct/Regular), `div_or_growth` (Growth/IDCW/Bonus), `investor` (Retail/Institutional), `parent_fund_house`, tax fields (`st_rate`, `st_period`, `lt_rate`, `lt_period`) | [src/tier1_derive.py](src/tier1_derive.py) |
| Tier 2 — returns metrics | NAV series from mfapi.in + benchmark series | `1Y/3Y/5Y/7Y CAGR`, `3Y rolling 2020..2026`, `Sharpe (3Y)`, `Beta`, `Tracking Error`, `Bench 3Y rolling 2020..2026` | [src/tier2_returns.py](src/tier2_returns.py) |
| Benchmark mapping | `sub_category` (+ scheme name for Index/ETF) | Either an AMFI scheme code (real index-fund TRI proxy) or a synthetic token (`FIXED_6.5`, `BLEND_65_35`, `BLEND_25_75`, …) | [src/benchmarks.py](src/benchmarks.py) |
| %ile rank within sub-category | `3Y rolling {year}` across all funds in the same `asset_subcategory` | `%ile rank 3Y rolling {year}` columns | [src/build_xlsx.py](src/build_xlsx.py) `add_percentile_ranks()` |

### Benchmark proxies (hardcoded scheme codes in [src/benchmarks.py](src/benchmarks.py))

These are AMFI scheme codes for index funds (Direct Growth) we use as TRI proxies, fetched via the same mfapi.in endpoint:

- `120716` — UTI Nifty 50 Index Fund
- `143341` — UTI Nifty Next 50 Index Fund
- `148726` — Nippon India Nifty Midcap 150 Index Fund
- `148519` — Nippon India Nifty Smallcap 250 Index Fund
- `152731` — Axis Nifty 500 Index Fund
- `149804` — Navi Nifty Bank Index Fund

Debt categories use a synthetic `FIXED_6.5` series (no clean free debt TRI). Hybrid categories use `BLEND_65_35` / `BLEND_25_75` (equity NAV × weight + fixed rate × remainder, daily-rebalanced).

## Field → source map (output columns)

Section ordering matches `SECTION_GROUPS` in [src/build_xlsx.py](src/build_xlsx.py).

### 1. Fund overview
| Column | Source |
|---|---|
| `schemeCode`, `schemeName` | input CSV |
| `isin_growth`, `isin_div_reinvest` | input CSV (`isinGrowth`, `isinDivReinvestment`) — both carried through; either may match a customer's CAS holding |
| `asset_class`, `asset_subcategory`, `plan_class`, `div_or_growth`, `investor` | Tier 1 derived |
| `min_investment`, `asset_size_cr` | **Groww detail** (`min_investment_amount`, `aum`) |

### 2. Quantitative — Fund Returns
| Column | Source |
|---|---|
| `1Y/3Y/5Y/7Y CAGR`, `3Y rolling {year}` | Tier 2 computed from mfapi.in NAVs |
| `%ile rank 3Y rolling {year}` | Tier 2 percentile within sub-category cohort |

### Benchmark returns
| Column | Source |
|---|---|
| `Bench 3Y rolling {year}` | Tier 2 computed from benchmark series (mfapi.in index fund or synthetic) |
| `Sharpe (3Y)`, `Beta`, `Tracking Error` | Tier 2 computed |

### 3. Portfolio Quality
| Column | Source |
|---|---|
| `top10_holdings_weight_pct` | **Groww detail** (`holdings[]` filtered to equity, top-10 `corpus_per` summed) |
| `portfolio_churn_l3y_pct` | **Groww detail** (`portfolio_turnover` — latest snapshot, used as L3Y proxy) |
| `size_exposure_LMS`, `style_exposure_GV`, `alpha_selection_l3y_pct`, `alpha_allocation_l3y_pct` | **Not sourced yet** — left null. Would need a holdings-classification source (Morningstar / FactSet / manual) for size/style; attribution analysis for alpha. |

### 4. Costs & Efficiency
| Column | Source |
|---|---|
| `expense_ratio_pct` | **Groww detail** (`expense_ratio`) |
| `exit_load`, `exit_load_period` | **Groww detail** (`exit_load` text, period parsed via regex `within \d+ (year\|month\|day)s?`) |
| `performance_fees` | constant — "Not applicable in Indian MFs" |
| `entry_load` | constant — "Nil (regulatory)" |

### 5. Team
| Column | Source |
|---|---|
| `lead_pm_name`, `manager_start_date` | **Groww detail** (`fund_manager_details[]`, current managers with no `date_to`, matched against `fund_manager` field) |
| `pm_turnover_l5y` | **Groww detail** (count of distinct PMs in `fund_manager_details` with `date_from` within last 5 years) |
| `parent_fund_house` | input CSV (`fundHouse`) |

### 6. Tax
All four (`st_rate`, `st_period`, `lt_rate`, `lt_period`) — Tier 1 rule-based, post-Apr-2023 Indian MF tax regime.

### Diagnostics
| Column | Source |
|---|---|
| `benchmark_token` | our pick (scheme code int or `FIXED_*`/`BLEND_*` token) |
| `groww_benchmark` | **Groww detail** (`benchmark_name`/`benchmark`) — useful for sanity-checking our mapping |
| `groww_isin_match` | bool — does Groww's `isin` equal either `isin_growth` or `isin_div_reinvest` for this scheme? |
| `_groww_slug` | Groww `search_id` we resolved to (for debugging mismatches) |
| `nav_start`, `nav_end`, `nav_points` | mfapi.in series span |

## Caching

| Cache | Path | Keyed by |
|---|---|---|
| NAV history | `build/cache/nav/{schemeCode}.json` | AMFI scheme code |
| Groww detail | `build/cache/scrape/{schemeCode}.json` | AMFI scheme code (after slug resolution) |

Both use raw JSON write-through; pass `force=True` to bypass. For a quarterly refresh, wipe `build/cache/` or use `force=True` selectively.

## Caveats for a production quarterly job

- **Groww endpoints are unofficial.** They can break or get rate-limited; add a graceful-fallback path before depending on them in prod.
- **Slug match isn't always exact.** `search_slug()` picks the best word-overlap match; verify with `groww_isin_match` and skip rows where it's False.
- **`portfolio_churn_l3y_pct` is the latest snapshot**, not a 3-year average — rename if that matters for the framework.
- **Size/style/alpha columns are unfilled**; decide on a paid source (Morningstar etc.) or drop the columns before publishing.
- **Index-fund proxies for TRI** drift slightly from the actual TRI index; acceptable for cohort-relative comparisons, less so for absolute benchmark return claims.
