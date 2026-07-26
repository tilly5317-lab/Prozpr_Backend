# SIP fund selection follows the rebalancing plan — design

**Date:** 2026-07-05 · **Status:** approved (audited, all findings dispositioned) · **Amended:** 2026-07-06 (per-fund cap, see bottom)
**Owner modules:** `AI_Agents/src/additional_investment` (engine), `app/domains/additional_investment` (caller), `app/domains/rebalancing` (new read function)

## Problem

The additional-investment SIP path recommends too many funds. Today it splits the
monthly amount across the nearest-unfunded bucket's subgroups, then `select_funds`
spreads each subgroup across the ranked-fund list under a per-fund cap that is a
percent of the (small) monthly amount — forcing the money across many funds.

Product intent: the SIP fund picks should be consistent with the trade
recommendations the customer sees from rebalancing. If rebalancing tells them to
BUY fund X in a subgroup, their SIP money for that subgroup goes into fund X — not
a spread of eight funds.

## Product decisions (settled 2026-07-05)

| # | Decision |
|---|----------|
| Q1 | Trades come from the **latest persisted rebalancing run** (`rebalancing_runs` by `created_at desc limit 1` for the user), not a fresh compute. |
| Q1.1 | No persisted run → **rank-1 fallback everywhere** (each subgroup's whole SIP share into that subgroup's rank-1 ranked fund). |
| Q2 | Only `action == BUY` trades count. Subgroup with no BUY → rank-1 fund gets its whole share. |
| Q3 | Multiple BUY funds in one subgroup → **equal split** of that subgroup's SIP share. |
| Q4 | **No per-fund caps** on the SIP path. Lumpsum keeps its caps. |
| Q5 | The subgroup split itself (nearest-unfunded-bucket targeting, `compute_targets`) is **unchanged** — only fund selection changes. |
| Q6 | Rebalancing BUYs in subgroups the SIP split gives ₹0 to are ignored. |
| — | Run **status is ignored** — all rebalancing plans are assumed accepted (pending/approved/executed/rejected all count; explicit product call). |

## Accepted trade-offs (audited 2026-07-05; deliberate, do not "fix")

- **Rounding overshoot.** Each fund's equal share is rounded to the nearest ₹100
  independently. Shares in the ₹50–99 band round up for every candidate, so a
  subgroup's buys can total above its target and the whole SIP can exceed the
  customer's stated monthly amount (e.g. ₹5,300 on a ₹5,000 ask). The pipeline's
  `undeployed = max(0, …)` clamp hides the overshoot. Accepted as-is.
- **Rejected runs count.** A run the customer set to `rejected` can still source
  the SIP funds. Accepted: all rebalancing plans are treated as accepted.
- **₹0 dust buys.** A subgroup share under ₹50 rounds to ₹0 and may emit/persist a
  ₹0/month buy row. Accepted as-is for very small SIPs.
- **Category-ask nuance.** A "SIP in small cap" reply lists top-ranked category
  funds while the actual buy may be a different (rebal-mirrored) fund. Accepted.

## Design

### App layer

1. **New read function** in `app/domains/rebalancing/services/` (small new module,
   e.g. `rebalancing_read_service.py`):
   `latest_buy_trades_by_subgroup(db, user_id) → tuple[run_id, dict[subgroup → list[isin]]] | None`
   - `user_id` is the **acting (effective) user id** — the same
     `ctx.effective_user_id` the ainv path already persists under, so a family
     member never sources funds from the primary account's run.
   - Latest `RebalancingRun` for that user: `created_at desc limit 1`, **no
     status filter**.
   - From its `rebalancing_trades`: `action == BUY` only, grouped by
     `asset_subgroup`, ordered by `amount_inr` desc within each group, deduped by
     ISIN.
   - Returns `None` when there is no run **or the latest run has zero BUY trades**
     (so "fallback ran" and the telemetry stay truthful — audit F4).
2. **`ainv_engine/service.py`** (`compute_additional_investment_result`): when
   cadence is `SIP_MONTHLY`, call the read function and pass the dict through the
   input builder. Lumpsum path untouched.
   - **Failure semantics (audit F5):** wrap the read in try/except — on any
     failure, log loudly and treat as `None` (rank-1 fallback everywhere, no
     `sip_rebal_run_id`). The rebalancing mirror is an *enhancement*, never a
     *gate*: a SIP recommendation must never fail because of it.
3. **`input_builder.build_additional_investment_input_for_user`** gains an optional
   `rebal_buy_isins_by_subgroup: dict[str, list[str]] | None = None` param,
   forwarded verbatim onto `AdditionalInvestmentInput`.

### Engine (`AI_Agents/src/additional_investment` — pure, no I/O)

4. `AdditionalInvestmentInput` gains
   `rebal_buy_isins_by_subgroup: dict[str, list[str]] | None = None`.
5. `pipeline.run_additional_investment`: cadence `SIP_MONTHLY` → new
   `select_funds_sip(targets, ranked_funds, rebal_buy_isins_by_subgroup,
   rounding_multiple)`. LUMPSUM (both deficit and legacy modes) keeps
   `select_funds` with caps, unchanged.
6. `select_funds_sip`, per `SubgroupTarget t`:
   a. `candidates` = `rebal_buy_isins_by_subgroup.get(t.subgroup, [])` filtered to
      ISINs present in **that subgroup's own ranked list** (not a flat global
      lookup — guards against a fund reclassified to a different subgroup in a
      newer ranking CSV), deduped, input order preserved (rebal BUY amount desc).
   b. Candidates non-empty → **equal split**: each of the k funds gets
      `_round_to_multiple(t.target_inr / k)` (nearest ₹100). Rounding drift is an
      accepted trade-off (see above). **Dust consolidation:** if the per-fund
      share rounds below ₹100, put the whole `_round_to_multiple(t.target_inr)`
      into the FIRST candidate instead (no spray of ₹0 rows); a whole-target
      round of ₹0 (target < ₹50) may still emit a single ₹0 buy — accepted.
   c. Candidates empty → the subgroup's **rank-1 ranked fund** gets
      `_round_to_multiple(t.target_inr)`.
   d. **Defensive guard:** a targeted subgroup with no ranked funds at all emits
      no buy and its amount surfaces in `undeployed_inr` — same silent
      under-deploy as today's `select_funds`. (Unreachable in production per
      audit — `sector_equities` is market-view-gated out — but the selector must
      not crash on it.)
   e. **No per-fund caps** on this path. `cap_pct_by_subgroup` /
      `default_cap_pct` are ignored for SIP.
   f. `FundBuy.reason`: `"Matches your rebalancing plan"` for (b),
      `"Top-ranked fund for this category"` for (c). Persisted for audit only —
      deliberately NOT surfaced to the chat formatter (audit F6: the facts pack
      drops `reason`, and no formatter change is made).
7. SIP monthly framing unchanged: buys get `monthly_amount_inr = amount_inr`.

### Persistence / telemetry

8. `AINV_ENGINE_VERSION` → `ainv-3.0.0` (SIP selection contract changed).
9. `request_extras["sip_rebal_run_id"] = str(run_id)` — **string, not UUID**
   (audit F4: a raw UUID breaks the JSONB `json.dumps` at flush and the
   best-effort except swallows it, silently dropping the whole persist). Absent
   when the rank-1 fallback ran (read function returned `None`).
10. `additional_investment_persist_service` is unchanged: it recovers
    rank/scheme_code by joining each buy's ISIN against `request.ranked_funds`,
    guaranteed to succeed because 6a filters candidates to the subgroup's ranked
    list.

### Formatter

11. **No formatter change.** (Explicit product call, audit F6: the LLM cannot see
    per-buy provenance and must not claim the SIP "matches your rebalancing plan".
    The goal of this feature is consistent fund logic, not narration.)

### Docs

12. `AI_Agents/Reference_docs/Logics_reference_docs/Additional_Investment.md` →
    v1.1: Step 2's fund-selection text must be rewritten for SIP (cap language
    becomes false there — SIP has no caps), Principle 6/cap claims scoped to
    lumpsum, and the new SIP rule added (SIP flows into the funds the customer's
    rebalancing plan is buying; equal split; top-ranked fund where the plan buys
    nothing).
    Also refresh the module `CLAUDE.md`s: two invariants in
    `AI_Agents/src/additional_investment/CLAUDE.md` become SIP-false ("fund
    selection is holding-agnostic", "per-fund cap keys off the DEPLOY amount"),
    and the src module-map line "SIP follows the ideal mix" needs the new
    selection rule.

## Tests

13. **Engine** (`AI_Agents/src/additional_investment/Testing/`): new
    `select_funds_sip` suite — equal split across k candidates; rank-1 fallback;
    stale-ISIN dropped (wrong-subgroup ISIN dropped); dedup; no caps applied;
    SELL/EXIT never consulted (input dict is BUY-only by construction, engine
    never sees actions); no-ranked-funds subgroup → no buy, undeployed; rounding
    behavior pinned as specified (including the accepted overshoot, the dust
    consolidation into the first candidate, and the single-₹0-buy case, as
    characterization tests). Pipeline: SIP routes through the new selector;
    lumpsum still through `select_funds`.
14. **App**: read-function tests (latest run, BUY-only, ordering, dedup, no-run →
    None, zero-BUY run → None); service wiring (SIP-only call, read failure →
    fallback + no crash); input-builder passthrough; persist round-trip through a
    real JSON/JSONB column (not fake-db) covering `sip_rebal_run_id`. Update
    existing tests: `test_sip_takes_no_snapshot_and_no_pin` (patch the new read
    function — its dummy db would otherwise AttributeError) and the hardcoded
    `ainv-2.0.0` engine-version assertion.
15. No prompt changes → prompt eval gate not strictly required, but cheap to run
    if other prompt work lands in the same branch.

## Audit trail

Adversarially audited 2026-07-05 (4 lenses × 21 findings × 2 refuters): F1
rounding overshoot — accepted; F2 rejected-run sourcing — accepted; F3 ₹0 dust
buys — accepted; F4 UUID/JSONB persist break — fixed (step 9); F5 missing failure
semantics — fixed (step 2); F6 formatter provenance blindness — resolved by
removing the formatter change (step 11); sector_equities crash claim — refuted
(market-view gate), defensive guard kept (step 6d).

## Amendment 2026-07-06 — SIP per-fund cap (supersedes Q4 "no caps")

Product call after reviewing the shipped behaviour ("entire share to rank-1 is
too concentrated"):

- **Per-fund cap on every SIP buy** (mirror AND fallback):
  `cap = max(cap_pct × monthly amount, AINV_SIP_FUND_CAP_FLOOR_INR)`.
  The percentages are the existing `cap_pct_for` values (10 default /
  20 multi_asset / 30 short_debt+arbitrage); the rupee floor (default ₹10,000,
  env `AINV_SIP_FUND_CAP_FLOOR_INR`) lives in `Rebalancing/config.py` with the
  other cap thresholds and reaches the engine via a new
  `AdditionalInvestmentInput.sip_fund_cap_floor_inr` field (input_builder wires
  it; engine default 0 = percentage-only).
- **Overflow walks down the subgroup's ranking**, each fund up to the cap,
  skipping funds already bought; ranking exhaustion → `undeployed_inr`.
- Selection is now two stages in `select_funds_sip`: equal split among rebal
  candidates clamped at the cap, then the rank walk with the remainder (with
  no candidates the walk IS the selection — old `select_funds` shape with the
  floored cap).
- Rationale: the floor keeps a small SIP concentrated in a handful of funds
  (a ₹10k SIP is never fragmented); the percentage keeps a large SIP from
  over-concentrating (a ₹5L SIP caps each fund at ₹50k).
- **Trade-off list changes:** the "₹0 dust buys" accepted trade-off is now
  moot — the capped walk skips sub-₹100 amounts, so a sub-₹50 target emits NO
  row (undeployed) instead of a ₹0 row. Equal-split rounding overshoot stands.
- Engine version → `ainv-3.1.0`.
- **Lumpsum floor (same day):** the identical rule applied to `select_funds`
  for BOTH lumpsum modes (deficit-fill and legacy no-holdings):
  `cap = max(cap_pct × lumpsum amount, AINV_LUMPSUM_FUND_CAP_FLOOR_INR)`
  (default ₹40,000, env-overridable; break-even at a ₹4L lumpsum under the
  10% default). New input field `lumpsum_fund_cap_floor_inr` (engine default
  0 = percentage-only, preserving old behaviour for direct callers). Engine
  version → `ainv-3.2.0`.
