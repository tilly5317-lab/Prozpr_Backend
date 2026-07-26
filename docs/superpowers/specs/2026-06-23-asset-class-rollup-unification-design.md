# Asset-class roll-up unification — design

- **Date:** 2026-06-23 (audited 2026-06-24)
- **Status:** Draft (awaiting review)
- **Scope:** Prozpr_Backend (primary) + Prozpr_Frontend (display-only changes)

## Problem

The app shows a user's portfolio allocation by asset class (Equity / Debt / Others /
Cash) in several places, and they disagree. The trigger case is a **multi-asset fund**:
it holds equity, debt and an "Others" sleeve internally, but every *current*-allocation
view dumps the whole fund into one bucket (Equity). Meanwhile chat reflects the
allocation engine's *target*, which **does** split multi-asset 65/25/10 — so the chat
answer and the Invest → Current vs Target view don't line up, and "Others" can read ₹0
on one surface while being non-zero on another.

## Current state — only the engine's *target* carves; every *current* is fund-level

| Surface | Source | Multi-asset fund | Carves? |
|---|---|---|---|
| **Chat — target/recommended mix** | engine `asset_class_breakdown` (note: `.actual` is a legacy **alias for `recommended`**), built by `split_with` | split 65/25/10 | **Yes (target only)** |
| **Chat — current mix** | `compute_current_asset_class_mix` (service.py:249) → sums `PortfolioAllocation` rows by `asset_class` | whole → Equity | **No** |
| **Current vs Target bars** — both columns (`RebalanceExplanation.tsx`) | rebalancing `subgroup_summaries` → `asset_class_for_subgroup` + frontend `toBucket`; `multi_asset` → Equity | whole → Equity | **No** |
| **Dashboard donut** (`CurrentAllocationCard.tsx`) | `GET /portfolio` `allocations` = `_derive_allocations` (live holdings, per-holding `classify_holding`) | whole → Equity | **No** |
| `AllocationChart.tsx` | hardcoded demo numbers | n/a — static | n/a |
| Chat `current_donut` tool | backend builder does not exist; `visualizations/registry.py` imports a missing module and nothing imports `registry.py` | dead code | n/a |

Two extra inconsistencies this surfaces:
- The carve rule lives only in `practical_asset_allocation/pipeline.py`
  `_build_asset_class_breakdown` → `split_with` (~L831): for the `multi_asset` subgroup,
  `eq = round(amt*comp.equity_pct/100)`, `oth = round(amt*comp.others_pct/100)`,
  `debt = amt - eq - oth`. `comp = multi_asset_composition`, default **65 / 25 / 10**.
- Chat's "current" reads **persisted `PortfolioAllocation` rows**, while the donut's
  "current" is recomputed **live from holdings** (`_derive_allocations` ignores the
  persisted rows). So the two "current" numbers can differ even before any carve.

## Goal

**One canonical asset-class roll-up, computed on the backend, used by every surface for
both the current and the target columns.** Multi-asset is always split 65/25/10. The
frontend only displays backend-provided buckets (the frontend never classifies funds).
Output vocabulary is four buckets: **Equity / Debt / Others / Cash**.

## Locked decisions

1. **Composition is the existing fixed assumption — 65 / 25 / 10** (`multi_asset_composition`).
   No per-fund real composition exists today; sourcing it is a separate, later project.
2. **Cash = bank balances only** — exactly the `Cash` bucket `compute_current_asset_class_mix`
   and `_derive_allocations` already produce. No fund is reclassified into Cash; the
   composition gains no `cash_pct`.
3. **Carve scope = the engine's `multi_asset` subgroup only**, split 65/25/10 — identical
   to what the engine already does for the target. Aggressive Hybrid / Balanced Advantage /
   Dynamic Asset Allocation keep their current mapping (Equity). Minimum change that makes
   every surface consistent.
4. **Carve on the backend; frontend renders.** `RebalanceExplanation.tsx` loses its
   client-side `toBucket` roll-up.
5. **Identify multi-asset via the canonical classifier (Option A).** `SUBCAT_TO_MAPPING`
   maps "Multi-Asset Allocation Fund" → `multi_asset`, so every surface shares one
   definition (see §2). Accepts the rebalancing per-fund-cap side effect.

## Design

### 1. Canonical function (single source of truth)

A backend function, e.g. `roll_up_asset_classes(items, bank_cash_inr, composition)`:

- **Input:** per holding `(asset_subgroup, asset_class, amount_inr)`, the bank-cash total,
  and a `MultiAssetFundComposition` (default 65/25/10).
- **Logic:** if `asset_subgroup == "multi_asset"` and `amount > 0`, split 65/25/10 into
  Equity/Debt/Others (debt takes the rounding residual, identical to `split_with`);
  else add the whole amount to its `asset_class`. Add `bank_cash_inr` to Cash.
- **Output:** ordered `{Equity, Debt, Others, Cash}` with ₹ and % (order mirrors `_ALLOC_ORDER`).
- **Home:** `app/domains/mutual_funds/services/` (e.g. `asset_class_rollup.py`), reusing
  `scheme_classification` constants. Refactor `split_with` to delegate to the same carve
  helper so there is genuinely one carve, not two copies.

### 2. Multi-asset identification — Option A (chosen)

There is **no DB tag** for multi-asset. `MfFundRating.asset_subgroup` is dead — never
populated (`transaction_service.py:53`, `latest_snapshot_service.py:197`,
`input_builder.py:72`). Held funds get their subgroup classified **live** via
`classify_holding(sub_category, name)`, which maps SEBI "Multi-Asset Allocation Fund" →
`("Equity", "medium_beta_equities")` — it never yields `multi_asset`. The only place
`multi_asset` is assigned today is the rebalancing fund-ranking list
(`input_builder.py:163`, `rank_row.asset_subgroup`, from `mf_subgroup_mapped.csv`).

**Decision: make the single canonical classifier emit `multi_asset`.** Change
`SUBCAT_TO_MAPPING["Multi-Asset Allocation Fund"]` from `("Equity", "medium_beta_equities")`
to `("Equity", "multi_asset")` (and confirm the raw normalisation "Multi Asset Allocation"
resolves to that key). Then `classify_holding` yields `multi_asset` for these funds
everywhere — donut, chat-current, and the rebalancing off-list path — from the
`MfFundMetadata.sub_category` we already store; no CSV coupling, no dead-column read.
`asset_class_for_subgroup("multi_asset")` already returns Equity, so any consumer that
does not carve is unaffected.

- **Scope (per decision #3):** only "Multi-Asset Allocation Fund" changes. Dynamic Asset
  Allocation / Balanced Advantage / Hybrids keep their current mapping (Equity).
- **Side effect to accept/verify:** held multi-asset funds now fall under the rebalancing
  per-fund cap `MULTI_FUND_CAP_SUBGROUPS = {multi_asset}`, changing rebalancing output for
  those funds (arguably more correct).
- **Rejected — Option B:** identify via the engine's ranking-CSV/disk-cache on the
  portfolio side. Exact bar match, but couples `/portfolio` to legacy CSV plumbing and
  needs each holding's ISIN.

### 3. Per-surface changes (feed all currents from live holdings + the canonical function)

- **`GET /portfolio` → dashboard donut.** `_derive_allocations` (`portfolio_router.py:157-197`)
  identifies multi-asset holdings per §2 and feeds the canonical roll-up. Cash carries
  through as today. `CurrentAllocationCard.tsx` needs no change — it displays what the API sends.
- **Chat current mix.** Re-source `compute_current_asset_class_mix` (service.py:249) from
  the same per-holding live data and the canonical function (instead of pre-aggregated
  `PortfolioAllocation` rows). This both adds the carve and removes the stale-vs-live gap;
  Cash is already one of its buckets.
- **Rebalancing Current vs Target.** Backend carves the `multi_asset` subgroup's
  `current_holding_inr` **and** `goal_target_inr` into eq/debt/others before exposing a
  per-asset-class current/target on the rebalancing detail response. `RebalanceExplanation.tsx`
  `buildDriftRows` drops `toBucket` and renders the API's buckets. This also aligns the
  bars' *target* with the engine's carved recommended that chat reflects.

### 4. Out of scope (flag, do not change here)

- Dead `app/domains/ai_engine/visualizations/registry.py` and its missing builder modules.
- Static demo numbers in `AllocationChart.tsx`.
- Sourcing real per-fund composition (replacing the 65/25/10 assumption).

## Risks / to verify during implementation

1. **One multi-asset definition (see §2).** The chosen identification must be the single
   source across donut, chat-current and the bars — verify the carved fund set is identical
   on all three. Under Option A, confirm the rebalancing `MULTI_FUND_CAP_SUBGROUPS` cap
   change is acceptable.
2. **Rounding parity.** Debt takes the residual; ensure the shared helper and `split_with`
   produce byte-identical integer splits.
3. **Persisted vs live current.** Re-sourcing `compute_current_asset_class_mix` from live
   holdings changes chat's current numbers (by design — it removes a stale source).
   Confirm nothing else depends on the old PortfolioAllocation-summed values.

## Acceptance criteria

- A portfolio holding a `multi_asset` fund produces **identical** Equity/Debt/Others ₹
  (to the rupee) across: chat's current mix, `GET /portfolio` allocations, and the
  rebalancing Current-vs-Target "current" column — and the bars' *target* matches the
  engine's carved recommended that chat shows.
- That fund's "Others" sleeve is non-zero on **all** surfaces (no longer ₹0 on bars/donut).
- Cash is a consistent fourth bucket (bank balances); no fund moves into Cash.
- Existing tests for `_derive_allocations`, the rebalancing service, and the
  asset-allocation chat pass; a new test asserts cross-surface parity on a fixture
  portfolio with a `multi_asset` holding.

## Key references

- Carve rule: `AI_Agents/src/practical_asset_allocation/pipeline.py` `_build_asset_class_breakdown` / `split_with` (~L790-869); `MultiAssetFundComposition` default 65/25/10 (`asset_allocation_pydantic/tables.py`).
- `asset_class_breakdown` model — `.actual` is an alias for `recommended` (the target, NOT current holdings): `AI_Agents/src/asset_allocation_pydantic/models.py:170-185`.
- Chat current mix: `app/domains/asset_allocation/services/aa_engine/service.py:249` `compute_current_asset_class_mix`; wired into facts at L396-399.
- Classifier: `app/domains/mutual_funds/services/scheme_classification.py` — `asset_class_for_subgroup`, `_ENGINE_ONLY_SUBGROUP_TO_ASSET_CLASS` (`multi_asset` → Equity), `ASSET_CLASS_*`.
- Multi-asset source: `MfFundRating.asset_subgroup` is DEAD/unpopulated (`transaction_service.py:53`, `latest_snapshot_service.py:197`, `input_builder.py:72`); `classify_holding` (`scheme_classification.py:493`) maps SEBI multi-asset → `medium_beta_equities`, never `multi_asset`; the engine's `multi_asset` comes from `rebal_engine/input_builder.py:163` (`rank_row.asset_subgroup`) via `mf_subgroup_mapped.csv`.
- Portfolio roll-up: `app/domains/portfolio/routers/portfolio_router.py` `_derive_allocations` (L157-197), `_holding_asset_class` (L117-139), `_ALLOC_ORDER` (L154); `PortfolioHolding.ticker_symbol` → `MfFundMetadata.scheme_code` (`models/portfolio.py:136`).
- Rebalancing: `subgroup_summaries` (`asset_subgroup`, `current_holding_inr`, `goal_target_inr`); `asset_class` computed property in rebalancing `schemas/__init__.py:56-57`.
- Frontend: `Prozpr_Frontend/src/pages/RebalanceExplanation.tsx` `buildDriftRows` (L98-148), `toBucket` (L35-41), bars (L611-693); `components/dashboard/CurrentAllocationCard.tsx`; `lib/api.ts` `PortfolioDetail`, `RebalancingRunDetail`.
- Dead code: `app/domains/ai_engine/visualizations/registry.py`.
