# AI_Agents/src/Rebalancing

Pure-Python rebalancing engine. Takes a goal-based ideal allocation plus present holdings, and emits per-fund target / buy / sell amounts under per-fund caps with a tax-aware sell prioritisation (STCG offset budget + carryforward losses). Mirrors the layout of `goal_based_allocation_pydantic`.

## Files

- `pipeline.py` — entry point: `run_rebalancing(request) → response`.
- `models.py` — `RebalancingComputeRequest`, per-step `FundRowAfterStepN` schemas, response/totals/warnings.
- `config.py` — env-overrideable knobs (caps, thresholds, tax rates).
- `tables.py` — static lookups (multi-cap sub-categories).
- `utils.py` — pure helpers (rounding, stcg/ltcg/exit-load math, tax estimate).
- `steps/` — one file per pipeline step (`step1_cap_and_spill` … `step6_presentation`).
- `Reference_docs/` — design docs and source workbook (planning only, not code).
- `Testing/` — pytest suite (per-step unit tests + e2e smoke + the 5-profile sweep).
- `Testing/Master_testing/` — dev-only end-to-end sweep harness (synthetic input builder + runner that drives the 5 canonical profiles through the engine and dumps `results/results.json` for the UI). Replaced by `app/services/ai_bridge/rebalancing_input_builder.py` + a real route when the production backend is built.

## Data contract

- Input: `RebalancingComputeRequest` — corpus, tax state, and a single homogeneous list of `FundRowInput` rows. Recommended funds carry `rank ≥ 1` (rank-1 holding the goal-allocation amount, ranks 2+ starting at 0). Held-but-not-recommended ("BAD") funds carry `rank = 0`, `is_recommended = False`, `target_amount_pre_cap = 0`. The input builder (upstream, in `app/services/`) is responsible for materialising both kinds.
- Output: `RebalancingComputeResponse` — rows after step 5, totals, trade list, warnings, metadata.

## Depends on

- `pydantic` only. No `src/` peer is imported.

## Tests

- Command: `pytest AI_Agents/src/Rebalancing/Testing -v`
- `test_e2e_workbook.py` is skipped pending fixture extraction from `Reference_docs/goal_based_allocation_model_latest.xlsx`.

## Env knobs (override via shell or process manager)

| Env var | Default | Drives |
| --- | --- | --- |
| `REBAL_MULTI_FUND_CAP_PCT` | `20.0` | Per-fund cap for multi-cap sub-categories |
| `REBAL_OTHERS_FUND_CAP_PCT` | `10.0` | Per-fund cap otherwise |
| `REBAL_MIN_CHANGE_PCT` | `0.10` | `worth_to_change` threshold |
| `REBAL_EXIT_FLOOR_RATING` | `5` | Force exit when rating below this |
| `REBAL_LTCG_EXEMPTION_INR` | `125000` | Annual LTCG exemption |
| `REBAL_STCG_RATE_EQUITY` | `20.0` | STCG % on equity |
| `REBAL_LTCG_RATE_EQUITY` | `12.5` | LTCG % on equity |
| `REBAL_ST_THRESHOLD_EQUITY` | `12` | ST→LT months for equity |
| `REBAL_ST_THRESHOLD_DEBT` | `24` | ST→LT months for debt FoF |

## Don't read

- `__pycache__/`, `Reference_docs/` cached artifacts (`*.xlsx` is source-of-truth for the e2e fixture, not application data).

## Refresh

If stale, run `/refresh-context` from this folder.
