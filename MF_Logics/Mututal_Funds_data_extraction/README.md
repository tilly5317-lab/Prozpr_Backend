# Mutual fund NAV pipeline (`Recurring_run`)

Fetches Indian mutual fund data from **[api.mfapi.in](https://api.mfapi.in)** and maintains:

1. A **scheme list** with latest NAV and dates (all schemes + “active” subset).
2. **Per-scheme historical NAV** (JSON) and one consolidated **tab-separated** history file.

**In the project root:** `latest_all_mf.csv`, `latest_nav_active.csv`, and **`mf_nav_history.txt`** (master TSV).  
**Under `NAV_level_data/`:** `latest.json`, per-scheme `{code}.json` history files, and `pipeline.log`.  
Optional seed JSON is read from `../MF_API/NAV_level_data/`.

## Requirements

- **Python 3.9+** (stdlib only; no `pip` dependencies).
- Network access to `api.mfapi.in`.
- Run scripts from this directory (or ensure `mf_pipeline_common.py` is importable on `PYTHONPATH`).

```bash
cd /path/to/Recurring_run
```

## Project layout

| File | Purpose |
|------|---------|
| `mf_pipeline_common.py` | Shared HTTP helpers, dates, and steps 1–4. Not run directly. |
| `extract_mf_funds.py` | **Part 1:** steps 1–2 (scheme universe + active filter). |
| `build_mf_nav_history.py` | **Part 2:** steps 3–4 (NAV history + `mf_nav_history.txt`). |

## Part 1 — Scheme list and active funds

```bash
python3 extract_mf_funds.py
```

**What it does**

- **Step 1:** Paginates `/mf`, then for each scheme code fetches `/mf/{code}/latest` → `NAV_level_data/latest.json`.
- **Step 2:** Writes **`latest_all_mf.csv`** and **`latest_nav_active.csv`** in this directory (every scheme vs. active-in-last-*N*-months, default **3**).

**Useful options**

| Option | Default | Meaning |
|--------|---------|---------|
| `--workers` | `12` | Concurrent HTTP threads for step 1. |
| `--active-months` | `3` | “Active” = latest NAV within this many months. |
| `--step 1` / `--step 2` | both | Run a subset (repeat `--step` if needed). |

**When to run:** Occasionally (weekly/monthly or when you need new listings). Part 2 reads **`latest_nav_active.csv`** from the project root.

## Part 2 — NAV history and master TSV

```bash
python3 build_mf_nav_history.py
```

**Prerequisite:** `latest_nav_active.csv` in the project root (from Part 1).

**What it does**

- **Step 3:** For each active scheme, incrementally updates `NAV_level_data/{schemeCode}.json`. Uses existing local files first, then optional seed: `../MF_API/NAV_level_data/{code}.json`.
- **Step 4:** Reads scheme-history JSON (`NAV_level_data/{code}.json` only — numeric filenames), joins sub-category from `latest_nav_active.csv`, writes **`mf_nav_history.txt`** here (TSV, header row).

**Useful options**

| Option | Default | Meaning |
|--------|---------|---------|
| `--workers` | `12` | Concurrency for step 3. |
| `--history-start` | `2023-01-01` | Start date (`YYYY-MM-DD`) for schemes with no prior JSON. |
| `--step 3` / `--step 4` | both | JSON only, or rebuild TSV only from existing JSON. |

**When to run:** Typical **daily** job after Part 1 has been run at least once (or whenever you refresh the active list).

## Outputs

| Location | Description |
|----------|-------------|
| **`latest_all_mf.csv`** (root) | All schemes + columns including `date_mm_dd_yyyy`. |
| **`latest_nav_active.csv`** (root) | Active subset + `sub_category`; dates as MM-DD-YYYY. |
| `NAV_level_data/latest.json` | Raw array from step 1 (latest NAV per scheme). |
| `NAV_level_data/{schemeCode}.json` | API-style payload: `meta`, `status`, `data` (history, newest first). |
| `NAV_level_data/pipeline.log` | Append log from both entry scripts. |
| **`mf_nav_history.txt`** (root) | TSV: `fund_house`, `scheme_code`, `scheme_category`, `Subcategory`, `scheme_name`, `date`, `nav`. |

## Scheduling

Example: run Part 2 nightly (adjust path):

```cron
0 22 * * * cd /path/to/Recurring_run && python3 build_mf_nav_history.py
```

Re-run Part 1 on your own cadence so `latest_nav_active.csv` stays aligned with new or newly active schemes.

## Operational notes

- **Stale active list:** If you only run Part 2 for a long time, the set of schemes in `latest_nav_active.csv` does not change; new funds will not appear until you run Part 1 again.
- **First run / seeds:** Step 3 can bootstrap history from `../MF_API/NAV_level_data/` when a local `NAV_level_data/{code}.json` is missing.
- Logs and large data files can grow; rotate or archive `NAV_level_data/pipeline.log` and historical outputs as needed.
