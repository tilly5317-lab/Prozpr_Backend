# Additional-investment deficit-fill for lumpsums — design

**Date:** 2026-07-03
**Status:** approved direction; pending implementation plan
**Scope:** `AI_Agents/src/additional_investment/` + `app/domains/additional_investment/` (lumpsum path only)

## Problem

`run_additional_investment` deploys the entire lumpsum into ONE bucket — the nearest
unfunded goal bucket (`ratio.py:17-27`, short → medium → long) — splitting it across
that bucket's subgroups only. The engine receives just two booleans
(`short_term_fulfilled`, `medium_term_fulfilled`); the rupee gap each goal still
needs is computed by the cashflow projection but discarded by the `all(g.is_funded)`
collapse in `_goal_funding_flags` (`ainv_engine/input_builder.py:81-82`).

Consequence: when the lumpsum exceeds what the nearest goal needs, the surplus
still piles into that one bucket — over-concentration and wrong funds. A second,
related defect: the engine deploys against the practical allocation computed on the
CURRENT corpus; the fresh money never reshapes the ideal it is deployed toward.

## Decision

For **lumpsum** deployments, replace single-bucket targeting with **deficit fill**:

> Run practical asset allocation (PAA) on the post-investment corpus
> (actual holdings value + lumpsum), compare each subgroup's ideal target with the
> customer's current holdings in that subgroup, and deploy the lumpsum into the
> positive gaps, proportionally. Buy-only; fund selection unchanged.

**SIP deployments keep today's single-bucket path, bit-for-bit.** A monthly SIP is
an ongoing commitment, not a one-time gap-fill; revisiting SIP behavior is out of
scope.

Decisions locked with the product owner (2026-07-03):

| Decision | Choice |
| --- | --- |
| Corpus basis for the ideal | **Actual holdings value + lumpsum** (holdings-pinned, mirrors `rebal_engine/input_builder.py:420-426`), NOT the profile-declared corpus |
| Holdings valuation source | **`PortfolioHolding.current_value`** summed per subgroup via the canonical `classify_holding()` (`scheme_classification.py:493-512`) |
| Recording the mode | **`request_input` JSONB** on `AdditionalInvestmentRun` — mode keys MERGED into the existing engine-input audit dump (see Persist below); NOT-NULL `target_bucket` column stays populated, derived from the deployed money's dominant horizon (see engine contract); no migration |

### Side note — broader direction (out of scope here)

Product owner direction: **practical asset allocation should generally run on
actual current holdings, not the customer-declared profile corpus.** This spec
applies that principle to the ainv lumpsum path only; migrating the main PAA /
asset-allocation flows to a holdings-based corpus is a separate future effort.

## How it works (lumpsum of ₹X)

```
1. Holdings snapshot      {subgroup → current ₹} from PortfolioHolding.current_value
                          + classify_holding(); ALSO returns the frozen values —
                          held ELSS (tax_efficient_equities) and direct stocks
                          (non_mf_equities); unclassifiable → value counts in the
                          corpus total, no gap row                       [NEW]
2. Post-investment ideal  PAA with total_corpus = holdings_total + X,
                          elss_corpus = held ELSS value,
                          non_mf_equity_corpus = held direct-stock value
                          (so PAA freezes/caps them properly instead of spreading
                          their value over buyable subgroups);
                          mf_corpus = holdings_total − stocks + X       [input change]
3. Gaps                   deficit_i = max(0, ideal_i − current_i) over eligible
                          subgroups; distribute X proportionally across positive
                          gaps                                           [NEW engine step]
4. Funds                  select_funds per subgroup (ranking, caps keyed off deploy
                          amount, spill-down, ₹100 rounding, undeployed) [UNCHANGED]
5. Persist                same 3 tables; JSONB mode fields; PAA run persisted is the
                          corpus+X counterfactual (correct lineage, flagged in JSONB)
6. Chat                   facts pack gains per-subgroup ideal/current/gap/buy rows;
                          formatter narrates gap-fill, not target_bucket
```

Both sides of the deficit (corpus total and per-subgroup values) come from the same
`PortfolioHolding` snapshot, so the math is internally consistent even when the
sync is stale. Signed gaps sum to **≈X** — exactly X only when every holding
classifies cleanly (unclassifiable holdings add their value to the corpus total
without a gap row, inflating the sum by that amount). The proportional scaling
step absorbs any residual: only X is ever deployed, distributed by relative gap
size. Feeding the frozen scalars (ELSS / direct stocks) to PAA is what keeps this
residual small — without them, PAA would spread locked value over buyable
subgroups and manufacture phantom gaps the size of those holdings.

## Contracts

### Engine (`AI_Agents/src/additional_investment/`)

- `AdditionalInvestmentInput` gains `current_value_by_subgroup: dict[str, float] | None = None`
  (default `None` → legacy behavior; every existing test stays green).
- `pipeline.run_additional_investment` branches: `cadence == LUMPSUM and
  current_value_by_subgroup is not None` → new `compute_deficit_targets()` in
  `ratio.py`; otherwise legacy `compute_targets()`.
- `compute_deficit_targets(subgroups, current_by_subgroup, deploy_amount, exclude_subgroups)`:
  - ideal_i = the subgroup's `total` column (post-investment PAA already includes X);
  - eligible = not in `exclude_subgroups` (ELSS `tax_efficient_equities`,
    `non_mf_equities` stay excluded, zero-weighted, renormalised — existing convention);
  - deficit_i = max(0, ideal_i − current_i); target_i = X × deficit_i / Σdeficit⁺;
  - **iteration direction is part of the contract**: iterate the IDEAL rows and
    look up current values with `current_by_subgroup.get(subgroup, 0.0)` — never
    the reverse. A held subgroup with no ideal row is thereby overweight by
    construction: no buy, no error (its value still shaped `holdings_total`).
    Both sides use the canonical `scheme_classification` subgroup vocabulary;
  - `ratio_i = target_i / X` (the subgroup's share of the deploy amount,
    = deficit_i / Σdeficit⁺) — preserves the legacy identity
    `target_inr = ratio × deploy_amount` across both modes;
  - **fallback**: Σdeficit⁺ == 0 → distribute by eligible ideal ratios (keeps
    building toward the ideal; covers the at/above-ideal-everywhere edge).
- Output model unchanged: `target_bucket` still emitted, but in deficit mode it is
  **derived from where the money actually went**, not from goal-funding flags:
  weight each subgroup's deployed rupees by its horizon composition (the
  `short_term`/`medium_term`/`long_term` columns relative to `total`) and label the
  run with the horizon receiving the most money. No cashflow projection needed.
- `short_term_fulfilled` / `medium_term_fulfilled` gain defaults (`False`) on
  `AdditionalInvestmentInput` — ignored on the deficit path, still required
  semantics for the SIP path (callers there keep passing real values).
- The app-layer input builder **skips `_goal_funding_flags` (the cashflow
  projection) entirely on the lumpsum deficit path** — its only consumer there was
  the old label. SIP keeps running it; the projection genuinely drives that path.
- Engine stays pure, float, buy-only, DB-free.

### App layer (`app/domains/additional_investment/`)

- **Holdings snapshot helper** (new, `ainv_engine/` package): reads the user's
  `PortfolioHolding` rows + fund metadata, classifies each via `classify_holding`,
  returns `(total_value, {subgroup: value}, frozen)` where `frozen` carries the
  held ELSS value (`tax_efficient_equities`) and the direct-stock value
  (`non_mf_equities`). Direct-stock rows are identified by `instrument_type ∈
  {equity, stock, share}` (the `allocation_rollup` convention), NOT lumped into
  "unknown". Unknown subgroup → counted in total, omitted from the map.
  (Cross-domain model read follows the existing `get_fund_ranking` import
  precedent.)
- **PAA corpus pinning**: for the lumpsum path the PAA input is set from the
  snapshot — `total_corpus = holdings_total + X`, `elss_corpus = frozen ELSS
  value`, `non_mf_equity_corpus = frozen stock value`, `mf_corpus =
  holdings_total − stock value + X` (explicit parameter threading, not the
  `additional_cash_inr` chat-override, so the what-if key is not leaked into the
  normal flow). Goals and all other inputs unchanged.
- **Persist**: `request_input` JSONB today stores the full engine-input audit dump
  (`request.model_dump(mode="json")`, `additional_investment_persist_service.py:68-72`)
  — that MUST be preserved. The mode keys are **merged** alongside it:

  ```python
  request_input = {
      **request.model_dump(mode="json"),   # existing audit dump — unchanged
      "deployment_mode": "deficit_fill",   # names verified not to collide
      "base_corpus_inr": holdings_total,   # with engine-input fields
  }
  ```

  The stored dict is thereby a superset of the engine input, no longer a pure
  round-trippable model dump. `additional_inr` is NOT stored separately —
  `deploy_amount_inr` is already in the dump. No schema change.
- **Facts pack / formatter**: lumpsum deficit runs add per-subgroup
  `{subgroup, ideal_inr, current_inr, gap_inr, buy_inr}` rows and switch the body
  prompt to the gap-fill narrative ("compared your current portfolio to the ideal
  for your goals including the new money; directed the money to the gaps").
  `target_bucket` is no longer narrated as the WHY on this path. When part of the
  deployment lands in emergency/liquid subgroups, the narrative should say so
  plainly ("part of this builds your emergency cushion — the foundation; the rest
  goes to your growth gaps") rather than leaving a liquid-fund buy unexplained.

## Edge cases

| Case | Behavior |
| --- | --- |
| Zero holdings (new customer) | corpus = X; every subgroup is a gap → full ideal split across ALL buckets (strictly better than today) |
| At/above ideal everywhere | Σdeficit⁺ = 0 → fallback: spread by eligible ideal ratios |
| Overweight subgroup | gap clamped to 0; no buy there; buy-only cannot trim it (accepted) |
| Unclassifiable holdings | value in corpus total; no gap row (inflates the gap sum — absorbed by proportional scaling) |
| Customer holds ELSS / direct stocks | their value informs the ideal via the frozen PAA scalars (`elss_corpus` / `non_mf_equity_corpus`); no gap rows for frozen subgroups; no fresh money deployed there |
| Emergency cushion unbuilt | deficit fill directs part of the lumpsum into liquid/emergency subgroups (`total` includes the emergency column) — DELIBERATE product decision (2026-07-04): the legacy "emergency is never a target" invariant ends for lumpsum deficit mode; cushion-building is the highest-priority gap |
| Stale portfolio sync | both sides shift together (same snapshot); accepted trade-off of the lighter source |
| SIP cadence | legacy path, untouched |
| Small gaps below ₹100 rounding | skipped by `select_funds` as today → `undeployed_inr` reporting unchanged |

## Testing

- **Engine unit tests** (`Testing/`): deficit math (clamp, proportional scale, sums,
  ratio identity: Σ ratios ≈ 1 and ratio × X ≈ target_inr),
  zero-holdings, all-overweight fallback, excluded subgroups renormalisation,
  unknown-subgroup omission, held-subgroup-absent-from-ideal (overweight by
  construction — no buy, no KeyError), dominant-horizon `target_bucket` derivation,
  SIP regression (legacy path byte-identical).
- **App-layer tests**: holdings snapshot helper (classification, unknown handling,
  frozen ELSS/stock extraction, empty portfolio); PAA input got `holdings_total + X`
  AND the frozen scalars (`elss_corpus`, `non_mf_equity_corpus`) from the snapshot;
  persist MERGES the JSONB mode keys alongside the engine-input dump (test asserts
  both the engine-input keys and the mode keys are present); the cashflow
  projection (`_goal_funding_flags`) is NOT invoked on the lumpsum deficit path;
  facts pack rows present on the lumpsum path and absent on SIP.
- Existing suites must stay green with `current_value_by_subgroup=None`.

## Dead-code review (audit 2026-07-04)

- **Nothing engine-side goes dead.** `select_target_bucket`, `compute_targets`,
  `_goal_funding_flags`, and the fulfilled-booleans all stay live — SIP still runs
  them. Do NOT delete them; they only LOOK orphaned from the lumpsum path. The day
  SIP switches to deficit-fill, this list becomes the deletion list.
- **Trim the legacy formatter body in this PR.** `_AINV_FORMATTER_BODY` becomes
  SIP-only (the deficit path gets its own body prompt); its lumpsum-specific lines
  ("lumpsum = a single one-time deployment", lumpsum framing of target_bucket)
  become dead prompt text describing a mode that can no longer reach it — remove
  them. Prompt text counts as code.
- **`used_cached_allocation` REMOVED (2026-07-04, product decision)**: ORM column,
  persist parameter, outcome field, and all ainv test references deleted; drop
  migration `e4f5a6b7c8d9` added. Rebalancing's twin is ALIVE (real cache) and
  untouched. DEPLOY ORDER: run the migration only with/after deploying this code —
  the older deployed backend still INSERTs the column.

## Out of scope

- SIP deficit-fill (revisit after lumpsum ships).
- Migrating the main PAA / asset-allocation flows to holdings-based corpus (side
  note above — separate effort).
- The category-aware chat feature ("smallcap only" answers) — separately specced
  conversation thread; sequenced after this engine fix.
- Selling / trimming overweight positions (that is rebalancing's job).
- Reconciling the `models.py` "rounded down" docstring vs nearest-₹100 code in
  `selection.py:21` — flagged, one-line cleanup to include in the implementation PR.
