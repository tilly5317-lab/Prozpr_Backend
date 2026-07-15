# Rebalancing — real fund-ranking CSV + per-fund buy/sell reasons

Date: 2026-05-19
Status: Draft (pre-implementation)

## Context

The rebalancing engine currently reads `AI_Agents/Reference_docs/Prozpr_fund_ranking.csv`. That file is a dummy: 5 columns, 173 hand-curated rows, no per-fund rationale. The data team has shipped a real ranking file — `prozpr_fund_ranking_may_2026.csv`, 16 columns, 11,825 rows — produced by an internal scoring pipeline that ranks every fund/ISIN under each subgroup and records the reason the fund was selected (for recommended ones) or rejected (for the rest).

We want two things from this switch:

1. The engine consumes the real file (not the dummy).
2. Every trade in the response carries the per-fund reason — `selection_reason` for buys, the joined `*_reason` columns for sells/exits — so the LLM can cite it when a customer asks "why this fund?" Reasons live only in the structured facts pack; the default rebalance brief stays unchanged.

## Current state

### CSV schemas

**Old (`Prozpr_fund_ranking.csv`, 173 rows):**
```
asset_subgroup, sub_category, rank, isin, recommended_fund
```
Every row is a recommended fund. Rank is always populated.

**New (`prozpr_fund_ranking_may_2026.csv`, 11,825 rows):**
```
asset_subgroup, sub_category, rank, isin, scheme_code, recommended_fund,
selection_reason, custom_reason,
pm_tenure_reason, returns_pctile_reason, consistency_reason,
direct_regular_reason, div_growth_reason, worst_perf_reason,
size_reason, excluded_subgroup_reason
```
Two row types:
- `rank` populated (1, 2, 3, …) → **recommended**. `selection_reason` is populated. The 9 rejection columns are empty.
- `rank` blank → **evaluated but rejected**. `selection_reason` is empty. One or more rejection columns are populated.

### Consumer map (verified by audit)

| Path | Role | Touch needed |
| --- | --- | --- |
| `app/services/ai_bridge/rebalancing/fund_rank.py` | Production CSV loader | Yes — major |
| `app/services/ai_bridge/rebalancing/input_builder.py` | Builds engine input from holdings + ranking | Yes — wire reasons |
| `AI_Agents/src/Rebalancing/models.py` | Pydantic models for engine | Yes — new fields |
| `AI_Agents/src/Rebalancing/steps/step6_presentation.py` | Builds `TradeAction`s | Yes — populate `fund_reason` |
| `app/services/ai_bridge/rebalancing/service.py` | LLM facts pack | Yes — add `reason` to `fund_actions` |
| `app/services/ai_bridge/rebalancing/formatter.py` | Default markdown brief | **No** — reasons are clarification-only |
| `app/services/ai_bridge/rebalancing/tests/test_fund_rank.py` | Pinned ISIN tests | Yes — repin + new test |
| `AI_Agents/src/Rebalancing/Testing/test_5_profile_smoke.py` | Dev smoke test (its own CSV path constant + bridge call site) | Yes — repoint path; pass `rejection_reasons` to `build_request`; assert `fund_reason` populated on BAD EXIT |
| `AI_Agents/src/Rebalancing/Testing/Master_testing/runner.py` | Dev sweep (its own CSV path constant + bridge call site) | Yes — repoint path; pass `rejection_reasons` to `build_request` |
| `AI_Agents/src/Rebalancing/Testing/Master_testing/bridge.py` | Dev sweep — independent CSV loader + `FundRowInput` builder | Yes — filter rank-blank, load reasons, wire into rows |
| `AI_Agents/src/Rebalancing/Testing/Master_testing/profiles.py` | Dev sweep fixtures (BAD fund identity) | Yes — replace BAD ISIN |
| `scripts/seed_rebalancing_test_data.py` | Dev DB seed (CSV path + iteration + User 2 holding) | Yes — repoint + filter + add holding |
| `AI_Agents/Reference_docs/CLAUDE.md` | Reference-docs index | Yes — filename update |

### Out of scope (audited, surgically left alone)

- `AI_Agents/src/asset_allocation_pydantic/tables.py:98` — passing comment reference to old filename. Doesn't drive runtime behavior.
- `AI_Agents/src/Rebalancing/Testing/Master_testing/nav_cache.py:4` — docstring only.
- `AI_Agents/src/Rebalancing/Testing/Master_testing/results/*.json` — sweep output artifacts; regenerated on next run.
- `.claude/settings.local.json` cached command allowlists referencing the old filename.
- The existing `rationales.py` reason codes (`add_to_target`, `cap_spill_buy`, `trim_over_target`, `exit_bad_fund`, `exit_low_rated`) and the `reason_code/title/text` fields on `TradeAction`. Those describe *why this trade* (e.g., trim vs cap-spill). The new `fund_reason` describes *why this fund*. They coexist.
- The old `Prozpr_fund_ranking.csv` file stays on disk — no destructive delete.
- The internal "BAD" engine label (`is_recommended=False`) — purely internal. The customer-facing reason text is what changes.

## Design

### Data-flow contract

```
prozpr_fund_ranking_may_2026.csv
   │
   ▼
fund_rank.py
   ├── get_fund_ranking()      → dict[subgroup, list[FundRankRow]]   (rank ≥ 1 only; carries selection_reason)
   └── get_rejection_reasons() → dict[isin, str]                     (rank-blank rows; columns joined)
   │
   ▼
input_builder.py
   recommended row  → FundRowInput.selection_reason = rank_row.selection_reason
   BAD row          → FundRowInput.rejection_reason = rejection_reasons.get(isin) or NOT_EVALUATED_REASON
   │
   ▼
engine steps 1-5  (auto-propagate via **r.model_dump())
   │
   ▼
step6_presentation.py
   BUY  → TradeAction.fund_reason = row.selection_reason
   SELL / EXIT  → TradeAction.fund_reason = row.rejection_reason
   │
   ▼
service.py build_rebal_facts_pack
   fund_actions[i].reason = per-fund text (LLM-visible, clarification-only)
```

`reason_code / reason_title / reason_text` on `TradeAction` stay as-is — they describe the trade action (top-up vs cap-spill vs trim vs exit) rather than the fund.

### Assumptions surfaced

1. **Column mapping is fixed.** Recommended rows are identified by non-blank `rank`. `selection_reason` is the only column read for them. We do **not** fall back to `custom_reason` for recommended rows; data inspection shows it's only populated for rejected rows.
2. **Rejection text is joined deterministically.** Non-empty rejection columns are concatenated with a single space in this order: `custom_reason, pm_tenure_reason, returns_pctile_reason, consistency_reason, direct_regular_reason, div_growth_reason, worst_perf_reason, size_reason, excluded_subgroup_reason`.
3. **`scheme_code` is unused.** The new column exists but no downstream consumer reads it. Not added to `FundRankRow` (YAGNI).
4. **"BAD" definition unchanged.** A BAD fund is still anything not in `rank ≥ 1`. Held funds appearing in rank-blank rows look up their joined rejection reason. Held funds absent from the CSV altogether use `NOT_EVALUATED_REASON`.
5. **Reasons are clarification-only.** They appear in the LLM facts pack and on `TradeAction`s, **not** in the default markdown brief built by `formatter.py`. The customer only sees a reason if they ask.
6. **Old CSV stays on disk.** Repoint references; do not delete the file. Easy rollback.

### File-by-file changes

#### 1. `app/services/ai_bridge/rebalancing/fund_rank.py`

- `_CSV_PATH` → `prozpr_fund_ranking_may_2026.csv`.
- `FundRankRow` gains `selection_reason: str` (defaults to `""`).
- `get_fund_ranking()`:
  - Reads `row["rank"]`, strips whitespace; if blank, skip this row.
  - Otherwise `int(rank_raw)` and append a row with `selection_reason = (row.get("selection_reason") or "").strip()`.
- New module-level constant:
  ```python
  NOT_EVALUATED_REASON = (
      "This fund didn't make it through our filtering criteria — we recommend exiting it."
  )
  ```
- New tuple `_REJECTION_COLUMNS` listing the 9 rejection column names in the fixed concat order.
- New cached function `get_rejection_reasons() -> dict[str, str]`:
  - Walks the same CSV. Skips rows where rank is non-blank.
  - For each rank-blank row, collects `(row.get(col) or "").strip()` for every column in `_REJECTION_COLUMNS`, drops empties, joins with `" "`.
  - If at least one non-empty piece exists, stores `out[isin] = joined`.
  - Returns `out`.

The CSV is opened twice in the worst case (once per cached function on first call). Acceptable for an ~11k-row file loaded once at startup.

#### 2. `AI_Agents/src/Rebalancing/models.py`

- `FundRowInput` gains:
  ```python
  selection_reason: Optional[str] = None
  rejection_reason: Optional[str] = None
  ```
  Since `FundRowAfterStep1..5` inherit from `FundRowInput` and every step builds the next via `**r.model_dump()`, these fields propagate to `FundRowAfterStep5` for free.
- `TradeAction` gains:
  ```python
  fund_reason: Optional[str] = None
  ```

#### 3. `app/services/ai_bridge/rebalancing/input_builder.py`

- Import additions: `get_rejection_reasons`, `NOT_EVALUATED_REASON` from `fund_rank`.
- After `ranking = get_fund_ranking()`, call `rejection_reasons = get_rejection_reasons()`.
- `_build_row` gains two optional kwargs: `selection_reason: Optional[str] = None`, `rejection_reason: Optional[str] = None`. Threads them through to the `FundRowInput(...)` constructor.
- Recommended-row branch: pass `selection_reason=rr.selection_reason or None`.
- BAD-row branch: pass `rejection_reason=rejection_reasons.get(isin) or NOT_EVALUATED_REASON`.

#### 4. `AI_Agents/src/Rebalancing/steps/step6_presentation.py`

- In `_trade_action_for`, after deciding `(action, reason)`:
  ```python
  if action == "BUY":
      fund_reason = r.selection_reason
  else:  # SELL or EXIT
      fund_reason = r.rejection_reason
  ```
- Pass `fund_reason=fund_reason` into `TradeAction(...)`.

#### 5. `app/services/ai_bridge/rebalancing/service.py` — `build_rebal_facts_pack`

- In the loop that builds each `fund_rows` entry, derive a `reason` string:
  - `buy > 0` → `getattr(action, "selection_reason", None) or ""`
  - else if `sell > 0` → `getattr(action, "rejection_reason", None) or ""`
  - else → `""`
- Add `"reason": reason` to the dict.
- Update the docstring shape comment to document the new key on `fund_actions`.

#### 6. `app/services/ai_bridge/rebalancing/tests/test_fund_rank.py`

- `test_first_row_is_aditya_birla_large_cap` → rename to `test_first_row_low_beta_equities` (or keep name but update the docstring) and pin to `INF109K016L0` (ICICI Prudential Large Cap, the new rank 1).
- Add `test_get_rejection_reasons_for_known_isin`: pick a known rank-blank ISIN from the new CSV (`INF209K01YY7` is a good candidate — Aditya Birla Large Cap was bumped from the recommended list); assert the returned dict has that ISIN and the value contains the expected rejection-reason substrings (e.g., "less than 3 years", "top 25%").

#### 7. Dev fixtures — `AI_Agents/src/Rebalancing/Testing/test_5_profile_smoke.py` and `Master_testing/runner.py`

- One-line change in each: CSV path → `prozpr_fund_ranking_may_2026.csv`.

#### 7b. Dev sweep loader — `AI_Agents/src/Rebalancing/Testing/Master_testing/bridge.py`

This file duplicates the CSV-loading + `FundRowInput`-building responsibilities of `fund_rank.py` + `input_builder.py` for the dev sweep. It is **not** wired through the production loaders, so the changes there don't reach it automatically. Note: we do not import from `app/services/ai_bridge/rebalancing/...` here — that would introduce a cross-layer dependency from a dev fixture into the FastAPI service layer.

- `load_ranking(csv_path)` (line 34):
  - Inside the `csv.DictReader` loop, gate on `row["rank"]`: `if not (row.get("rank") or "").strip(): continue`. Without this, `int(row["rank"])` crashes on the new CSV's rank-blank rows.
  - Add `"selection_reason": (row.get("selection_reason") or "").strip()` to the dict pushed into `by_sg`.
- Add a sibling helper at module scope:
  - `_REJECTION_COLUMNS = (...)` — same 9 column names + same order as the production `get_rejection_reasons` (local copy; duplicated three-line constant is fine for a dev fixture).
  - `_NOT_EVALUATED_REASON_DEV = "This fund didn't make it through our filtering criteria — we recommend exiting it."` — same text as production `NOT_EVALUATED_REASON` (duplicated string is fine; the two layers are intentionally decoupled).
  - `def load_rejection_reasons(csv_path: Path) -> dict[str, str]`: walks the CSV, processes only rows where rank is blank, joins non-empty rejection columns with `" "`, returns `{isin: joined}`.
- `build_request` signature (line 79): add a new kwarg `rejection_reasons: dict[str, str] | None = None`. When `None`, BAD-row construction uses `_NOT_EVALUATED_REASON_DEV` for every BAD fund (no per-ISIN lookup).
- Inside `build_request`:
  - At line 111 (recommended branch): pass `selection_reason=r.get("selection_reason") or None` into `FundRowInput(...)`.
  - At line 139 (BAD branch): compute `rejection_reason = (rejection_reasons.get(h.isin) if rejection_reasons else None) or _NOT_EVALUATED_REASON_DEV` and pass it into `FundRowInput(...)`.

#### 7c. Sweep & smoke-test call sites — `runner.py` and `test_5_profile_smoke.py`

- `runner.py`: at line 200 (right after `ranking = load_ranking(_RANKING_CSV)`), add `rejection_reasons = load_rejection_reasons(_RANKING_CSV)`. At line 209, append `rejection_reasons=rejection_reasons` to the `build_request(...)` kwargs.
- `test_5_profile_smoke.py`: add a module-scope fixture mirroring `ranking` (or compute inline in `build_request` call); pass `rejection_reasons=...` to `build_request`. Also add one new assertion: `assert bad_trade.fund_reason`, asserting the EXIT trade carries a populated rejection reason. This locks the end-to-end contract.

#### 8. Dev fixtures — `AI_Agents/src/Rebalancing/Testing/Master_testing/profiles.py`

- `BAD_ISIN = "INF179K01YV8"` (HDFC Large Cap) is now rank-2 *recommended* — replace with a truly off-list ISIN.
- Pick an ISIN that's either (a) not in the new CSV at all, or (b) in the new CSV rank-blank under `low_beta_equities` so the rejection-reason path is exercised. Preferred: (b) — gives a real rejection reason in the smoke output.
- Recommend `INF209K01YY7` (Aditya Birla Large Cap, now rank-blank, has rejection reasons populated). Update `BAD_ISIN`, `BAD_FUND_NAME`, and the comment at line 31-34 accordingly.
- Update the line-80 docstring to reference the new filename.

#### 9. Dev seed script — `scripts/seed_rebalancing_test_data.py`

- Line 355: CSV path → new filename.
- Around line 369 (the seeding `for row in csv.DictReader(f):` loop): add `if not (row.get("rank") or "").strip(): continue` so only recommended (rank ≥ 1) funds get NAVs seeded. Without this, the script would seed 11,825 funds instead of ~170 recommended ones.
- Line 23 docstring + line 112-113 User-2 holdings: keep the Aditya Birla holding (now exercises the rejection-reason path) AND add a second User-2 holding for ICICI Prudential `INF109K016L0` (now the rank-1 recommended Large Cap). Update the docstring at line 23 to explain User 2 holds one recommended + one rejected fund so both paths are exercised in the dev DB.

#### 10. Doc — `AI_Agents/Reference_docs/CLAUDE.md:9`

- Update filename from `Prozpr_fund_ranking.csv` to `prozpr_fund_ranking_may_2026.csv`.

## Verification

Three gates, all must pass before declaring done:

1. **Production bridge tests**
   ```
   pytest app/services/ai_bridge/rebalancing/tests -v
   ```
   All existing tests green; updated `test_fund_rank.py` pin passes; new `test_get_rejection_reasons_for_known_isin` passes.

2. **Engine unit tests**
   ```
   pytest AI_Agents/src/Rebalancing/Testing -v
   ```
   All existing tests green. (None pin specific ISINs from the production CSV — they construct `FundRowInput`s directly.)

3. **End-to-end dev sweep**
   ```
   cd AI_Agents/src/ && python -m Rebalancing.Testing.Master_testing.runner
   ```
   Completes without error (without bridge.py fixes it crashes at `int(row["rank"])` on the first rank-blank row in the new CSV). The new `assert bad_trade.fund_reason` in `test_5_profile_smoke.py` automates the "EXIT trade carries a rejection reason" check; for BUY-side coverage, spot-check `results/results.json` for at least one `TradeAction` with `action == "BUY"` and a non-empty `fund_reason`.

## Risks

- **Cached recommendations in DB lack `fund_reason`.** Older `RebalancingRecommendation` rows persisted before this change won't have the field. Pydantic deserialization treats Optional-with-default as missing → `None`. Acceptable — no migration needed.
- **`get_fund_ranking()` is `@cache`'d at process scope.** Tests that need to swap the CSV must already be calling `get_fund_ranking.cache_clear()` (the existing pattern; documented in the docstring). New `get_rejection_reasons()` follows the same contract.
- **The dev seed script's User-2 scenario changes meaning.** Was "user holds the recommended fund"; now "user holds one recommended + one rejected fund". Comment update at line 23 makes this explicit so future devs don't get confused.
- **Sweep output snapshots will diverge from `_archive_per_profile/`.** Expected and acceptable — those are historical snapshots from earlier engine runs, not regression fixtures.
