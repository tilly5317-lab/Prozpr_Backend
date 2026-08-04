# AI_Agents/src/financial_primitives

Shared, deterministic financial-math kernel: pure functions, no LLM calls and no I/O. The public API is flat — consumers import straight from the package (`from financial_primitives import xirr`), re-exported by `__init__.py`.

## Files
- `__init__.py` — flat public API; re-exports every primitive below and defines `__all__`.
- `time_value.py` — time-value-of-money primitives (`future_value`, `present_value`, `compound`).
- `annuity.py` — annuity/loan primitives over `numpy_financial` (`pmt`, `rate`, `ipmt`, `RATEConvergenceError`).
- `inflation.py` — inflation primitives (`inflate`, `real_rate`).
- `dates.py` — Indian Financial Year date helpers (`fy_for_date`, `fy_end_after`, `eomonth`, `year_fraction`).
- `retirement.py` — closed-form retirement corpus (`retirement_corpus_pv`).
- `xirr.py` — extended IRR for irregular dated cashflows (`xirr`).
- `twr.py` — time-weighted-return wealth index (`twr_wealth_index`).
- `returns.py` — trailing point-to-point return (`cagr`): annualises start→end NAV growth to a CAGR %, used for fund track-records.

## Gotchas & invariants
- **No single day-count basis.** `xirr` annualises on `_DAYS_PER_YEAR = 365.0` (`xirr.py`) while `dates.year_fraction` uses calendar-days / 365.25 (`dates.py`). Deliberately different — don't "unify" them without checking each caller.
- **Sign conventions are the contract — and `xirr` and `twr` are opposites.** `xirr` is investor-centric: a purchase is negative, a redemption positive (`xirr.py`). `twr` is portfolio-centric: that same purchase is a *positive* external cashflow, a redemption negative (`twr.py`). Feed one transaction ledger to both without flipping the sign and the wealth index silently double-counts every contribution. `annuity`: positive principal in, positive payments out (the `pmt`/`ipmt` wrappers flip the sign internally).
- **`RATEConvergenceError` has two triggers** — non-convergence *and* an economically implausible solved rate, not just failure to converge (`annuity.py`).

## Shared library
Library, not an agent — no pipeline, not an LLM tool. Imported cross-agent (e.g. `cashflow_statement/`) and by the app layer (`xirr` powers `app/domains/portfolio/services/benchmark_service.py` and the mutual-funds XIRR service; `twr_wealth_index` powers `app/domains/portfolio/services/twr_service.py`) — the documented exception to "agents don't import each other."

## Don't read
- `__pycache__/`.
- `Testing/` — pytest suite.
