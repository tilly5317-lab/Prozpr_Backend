# MF_Logics/ — LEGACY (mostly)

Historical mutual-fund data-extraction and mapping pipelines:
`Mututal_Funds_data_extraction/` and `Mututal_Fund_Mapping_AA_Internal/`. Both
are pre-production exploration work; active MF flows live under
`app/services/` and `AI_Agents/src/`.

## Imported by active code?

PARTIALLY. The classification *logic* has been promoted to a live module —
`app/domains/mutual_funds/services/scheme_classification.py` — which is the
single source of truth for `sub_category → asset_subgroup → asset_class`.
What still lives in this folder:

- **`Mututal_Funds_data_extraction/mf_subgroup_mapped.csv`** — IS read at
  runtime by the rebalancing engine
  (`app/domains/rebalancing/services/rebal_engine/_disk_cache.py:_load_meta_table`).
  The CSV is now a *frozen snapshot* of the live classifier's output; it does
  not encode any new logic. Migration to a DB-backed read is tracked in the
  `TODO(DB-backed)` at the top of `_disk_cache.py`.
- **`Mututal_Funds_data_extraction/latest_nav_active.csv`** — IS read at
  runtime by the same `_disk_cache.py` for NAV lookups.
- **`Mututal_Funds_data_extraction/generate_mf_subgroup_mapping.py`** — the
  historical generator that produced `mf_subgroup_mapped.csv`. **Do not run
  it now**; its `SUBCAT_TO_MAPPING` dict has been ported (with corrections
  for the orphan `"Others"` subgroup and the `Arbitrage Fund` asset_class) to
  `scheme_classification.SUBCAT_TO_MAPPING`. Keep this file for historical
  reference only.

Everything else in this tree is exploration / archive.

## When to touch this

Don't, with one narrow exception: if `mf_subgroup_mapped.csv` needs a refresh
to reflect new AMFI schemes, regenerate it via the live classifier (port the
generator's CSV-writing loop to call `classify_holding(sub_category,
scheme_name)`) — don't run the legacy script.

## Don't read unless

- You want a historical mapping reference.
