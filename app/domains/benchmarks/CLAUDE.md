# app/domains/benchmarks/ — market benchmark index data (catalogue + daily EOD values, scraper, scheduler, read API)

Owns all benchmark *data*. Portfolio analytics that *consume* it (TWR, portfolio-vs-Nifty) live in `portfolio/` and read from here via `benchmark_data_service`.

## Layers

- **models/** — `BenchmarkIndex` (catalogue: code/display/provider/asset_class/is_active, e.g. NIFTY 50) + `BenchmarkIndexValue` (daily EOD `tri_value`/`ntr_value`/reserved `pr_value`, FK to catalogue, unique per index+date).
- **schemas/** — `BenchmarkIndexSummary` (catalogue row + latest value), `BenchmarkValueRow`, `BenchmarkHistoryResponse`.
- **routers/** — `/benchmarks` (list), `/benchmarks/{code}/history`, `/benchmarks/{code}/latest`. Auth required, not family-scoped (data is global).
- **services/** — `niftyindices_fetcher` (the scraper), `benchmark_data_service` (catalogue + upsert + refresh + reads + scheduled job), `benchmark_scheduler` (thrice-daily job).

## Gotchas & invariants

- **"tri scheduler" = thrice-daily, not Total-Return-Index.** `benchmark_scheduler` fires `run_benchmark_refresh_job` 3×/day (09:30/14:30/21:30 IST, `BENCHMARK_REFRESH_HOURS`); each run re-fetches a short trailing window and upserts so the day's EOD (and any NSE revision) is captured. Gated by `BENCHMARK_SCHEDULER_ENABLED` (default ON; legacy `INDEX_TRI_SCHEDULER_ENABLED` still honoured). Serialized across workers by Postgres advisory lock `BENCHMARK_LOCK_KEY=7421101` (`services/benchmark_data_service.py:42`; scheduler wiring in `services/benchmark_scheduler.py` + `app/core/lifespan.py`).
- **Upsert is DO UPDATE, not DO NOTHING** — a later run overwrites a date's value (`bulk_upsert_values`, `services/benchmark_data_service.py`). Differs from the MF NAV/old-TRI DO-NOTHING posture on purpose.
- **EOD lag is intentional.** Reads use "nearest `value_date <= on`" (`get_value_on_or_before`), so a lookup for *today* returns yesterday's EOD until tonight's close publishes. The portfolio comparison/TWR rely on this.
- **First refresh bootstraps an empty index** with a `BACKFILL_YEARS` (5) backfill; otherwise it only refreshes `DEFAULT_LOOKBACK_DAYS` (10).
- New index → add a row to `KNOWN_INDICES` (catalogue metadata) so `get_or_create_index` can auto-seed it (`services/benchmark_data_service.py`).

## Don't read

- `__pycache__/`.
