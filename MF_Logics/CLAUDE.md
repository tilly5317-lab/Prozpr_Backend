# MF_Logics/ — legacy MF data work, plus two runtime CSVs

Historical mutual-fund data-extraction and mapping work; pre-production exploration. Active MF flows live under `app/` and `AI_Agents/src/` — classification *logic* was promoted to `app/domains/mutual_funds/services/scheme_classification.py`, the single source of truth for `sub_category → asset_subgroup → asset_class`. NOT a pure archive: two CSVs here are read at runtime (below), which is why this isn't a Stub.

## Child modules

- **Mututal_Funds_data_extraction/** — extraction scripts + the two RUNTIME CSVs (see Gotchas).
- **Mututal_Fund_Mapping_AA_Internal/** — historical mapping work.
- **Index_scrapper/** — historical index-scraping work.
- **MF_fund_view_data/** — fund-evaluation/ranking pipeline (`src/` fetches NAV + Groww data into a ranking workbook; see its `DATA_SOURCES.md`).

## Gotchas & invariants

- **`mf_subgroup_mapped.csv` + `latest_nav_active.csv` are runtime inputs** — read by the rebalancing engine (`app/domains/rebalancing/services/rebal_engine/_disk_cache.py:_load_meta_table`) for fund metadata + NAV. Frozen snapshots of the live classifier's output; DB migration tracked in `TODO(DB-backed)` atop `_disk_cache.py`.
- **`generate_mf_subgroup_mapping.py` is reference-only — do not run it.** Its `SUBCAT_TO_MAPPING` was ported (with `"Others"` / `Arbitrage Fund` corrections) to `scheme_classification.SUBCAT_TO_MAPPING`. If the CSV needs a refresh for new AMFI schemes, regenerate via the live classifier (`classify_holding`), not the legacy script.

## Don't read

- Everything else here — historical exploration, not consulted by active code.
