# MF_Logics/ — LEGACY (mostly)

Historical mutual-fund data-extraction and mapping work: `Mututal_Funds_data_extraction/`, `Mututal_Fund_Mapping_AA_Internal/`, `Index_scrapper/`, and `MF_fund_view_data/` (fund-evaluation/ranking pipeline — `src/` fetches NAV + Groww data into a ranking workbook; see its `DATA_SOURCES.md`). Pre-production exploration; active MF flows live under `app/` and `AI_Agents/src/`.

## Imported by active code?

PARTIALLY. Classification *logic* was promoted to `app/domains/mutual_funds/services/scheme_classification.py` — the single source of truth for `sub_category → asset_subgroup → asset_class`. Still live in this folder:

- **`Mututal_Funds_data_extraction/mf_subgroup_mapped.csv`** + **`latest_nav_active.csv`** — read at runtime by the rebalancing engine (`app/domains/rebalancing/services/rebal_engine/_disk_cache.py:_load_meta_table`) for metadata + NAV. The CSV is a frozen snapshot of the live classifier's output, no new logic. DB migration tracked in `TODO(DB-backed)` atop `_disk_cache.py`.
- **`Mututal_Funds_data_extraction/generate_mf_subgroup_mapping.py`** — historical generator. **Do not run it**; its `SUBCAT_TO_MAPPING` was ported (with `"Others"` / `Arbitrage Fund` corrections) to `scheme_classification.SUBCAT_TO_MAPPING`. Reference only.

## When to touch this

Don't, with one exception: if `mf_subgroup_mapped.csv` needs a refresh for new AMFI schemes, regenerate via the live classifier (`classify_holding`) — not the legacy script.
