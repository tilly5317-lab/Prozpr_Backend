# AI_Agents/src/Rebalancing

Pure-Python rebalancing engine. Takes a goal-based ideal allocation plus present holdings, and emits per-fund target / buy / sell amounts under per-fund caps with a tax-aware sell prioritisation (STCG offset budget + carryforward losses). Mirrors the layout of `asset_allocation_pydantic`.

## Files

- `pipeline.py` — entry point: `run_rebalancing(request) → response`.
- `models.py` — `RebalancingComputeRequest`, per-step `FundRowAfterStepN` schemas, response/totals/warnings.
- `config.py` — env-overrideable knobs (caps, thresholds, tax rates).
- `tables.py` — static lookups (multi-cap sub-categories).
- `utils.py` — pure helpers (rounding, stcg/ltcg/exit-load math, tax estimate).
- `rationales.py` — customer-facing rationale strings (title + body) keyed by `reason_code`. Single source of truth for both the dev sweep and the future production customer-view adapter.
- `steps/` — one file per pipeline step (`step1_cap_and_spill` … `step6_presentation`).
- `Reference_docs/` — design docs and source workbook (planning only, not code).
- `Testing/` — pytest suite (per-step unit tests + e2e smoke + the 5-profile sweep).
- `Testing/Master_testing/` — dev-only end-to-end sweep harness (synthetic input builder + runner that drives the 5 canonical profiles through the engine and dumps `results/results.json` for the UI). Replaced by `app/services/ai_bridge/rebalancing_input_builder.py` + a real route when the production backend is built.

## Data contract

- Input: `RebalancingComputeRequest` — corpus, tax state, and a single homogeneous list of `FundRowInput` rows. Recommended funds carry `rank ≥ 1` (rank-1 holding the goal-allocation amount, ranks 2+ starting at 0). Held-but-not-recommended ("BAD") funds carry `rank = 0`, `is_recommended = False`, `target_amount_pre_cap = 0`. The input builder (upstream, in `app/services/`) is responsible for materialising both kinds.
- Output: `RebalancingComputeResponse` — rows after step 5, totals, trade list, warnings, metadata.
- **ELSS is scalar, not row.** ELSS exposure is surfaced via `RebalancingComputeRequest.practical_allocation_input.elss_corpus` (and echoed on `practical_allocation.corpus_breakdown.elss_corpus_inr`). ELSS rows are filtered out of `rows` by the upstream input builder. `step6` emits a frozen `SubgroupSummary` for `tax_efficient_equities` so the customer view still shows the ELSS allocation, but no `BUY`/`SELL`/`EXIT` trade is ever generated for it (SEBI 3-year lock-in).
- **Non-MF equity is scalar, not row.** Direct-stock / PMS holdings live on `practical_allocation_input.non_mf_equity_corpus`. When the practical engine's NFA-banded cap forces a trim, `step6` emits a single `SELL_DIRECT_STOCKS` `TradeAction` for `excess_direct_stocks_inr`. No per-stock trades.

## Depends on

- `pydantic`.
- `practical_asset_allocation` (documented peer-isolation exception per
  `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md`
  §B.1 / §C.3): `pipeline.run_rebalancing` calls
  `practical_asset_allocation.run_practical_allocation` first, then lifts
  per-subgroup targets from its `aggregated_subgroups` onto rank-1 fund rows.
  The practical output is also surfaced verbatim on `RebalancingComputeResponse.practical_allocation`.

## Tests

- Command: `pytest AI_Agents/src/Rebalancing/Testing -v`
- `test_e2e_workbook.py` is skipped pending fixture extraction from `Reference_docs/goal_based_allocation_model_latest.xlsx`.

## Env knobs (override via shell or process manager)

| Env var | Default | Drives |
| --- | --- | --- |
| `REBAL_MULTI_FUND_CAP_PCT` | `20.0` | Per-fund cap for multi-cap sub-categories |
| `REBAL_OTHERS_FUND_CAP_PCT` | `10.0` | Per-fund cap otherwise |
| `REBAL_SHORT_DEBT_FUND_CAP_PCT` | `30.0` | Per-fund cap for short_debt subgroup (Excel R247) |
| `REBAL_ARBITRAGE_FUND_CAP_PCT` | `30.0` | Per-fund cap for arbitrage / arbitrage_plus_income subgroups |
| `REBAL_MIN_CHANGE_PCT` | `0.10` | `worth_to_change` threshold |
| `REBAL_EXIT_FLOOR_RATING` | `5` | Force exit when rating below this |
| `REBAL_LTCG_EXEMPTION_INR` | `125000` | Annual LTCG exemption |
| `REBAL_STCG_RATE_EQUITY` | `20.0` | STCG % on equity |
| `REBAL_LTCG_RATE_EQUITY` | `12.5` | LTCG % on equity |
| `REBAL_ST_THRESHOLD_EQUITY` | `12` | ST→LT months for equity |
| `REBAL_ST_THRESHOLD_DEBT` | `24` | ST→LT months for debt FoF |

## Don't read

- `__pycache__/`, `Reference_docs/` cached artifacts (`*.xlsx` is source-of-truth for the e2e fixture, not application data).
