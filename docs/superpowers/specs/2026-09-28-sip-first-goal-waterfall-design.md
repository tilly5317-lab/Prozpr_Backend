# SIP-first goal funding (backend logic) — design

Date: 2026-09-28 (revised after audit)
Branch: `rebal_logic_changes_0920` (merges later into `feat-central_investment_preference`)
Modules: `AI_Agents/src/asset_allocation_pydantic`, `AI_Agents/src/practical_asset_allocation`,
`AI_Agents/src/additional_investment`, `AI_Agents/src/Rebalancing`, `AI_Agents/src/cashflow_statement`,
`app/domains/{asset_allocation,practical_asset_allocation,additional_investment,rebalancing}`,
`AI_Agents/lifecycle_sim_testing` (dev-only)
Companion docs: team summary "SIP-First Goal Funding — Architecture" (Claude Doc); chat and
facts-pack changes live in a separate spec, "SIP-First Goal Funding — Chat & Facts Pack".

---

## Why

Two engines decide whether a near goal is funded, by different rules. The allocation engine
(step 2) moves today's cost of every short-term goal from the corpus into debt and ignores the
SIP. The SIP path asks the cashflow projection (`ainv_engine/input_builder.py::_goal_funding_flags`),
which counts corpus + future SIP + returns, and sends 100% of the SIP to either the short-term or
the long-term mix. When the SIP path reads a goal as funded, the SIP buys equity while each
rebalance sells equity to refill the debt pot.

This change replaces both with one rule, computed once in step 2, and read by rebalancing, SIP
and lumpsum.

## Rule

At every review, short-term goals are funded nearest first from three sources, in order:

1. **Existing short-term money** — every debt and arbitrage fund already held (not
   income-plus-arbitrage) — credited to goals nearest first.
2. **The SIP, front-loaded** — the whole SIP goes to the nearest uncovered goal until it is
   covered, then the next. Never proportional.
3. **The corpus** — only what the SIP cannot deliver by a goal's date is moved from long-term
   holdings into debt now.

Around it:

- SIP left after every short-term goal is covered goes to the long-term mix.
- A **lumpsum** fills the corpus part of the goal gap (step 3) first, then goes to long-term.
- **A held debt fund is never sold to buy another debt fund.** Short-term money beyond what the
  goals need joins the long-term portfolio and may be sold into other asset classes as usual.
- Goals are valued at future value. No return is assumed anywhere; growth shows up at the next
  review.
- Customers with a saved preference are unchanged (step 2 is already suspended for them).
- Goals beyond the short-term window get the long-term mix, as today; the window itself
  (24 months + `months_to_fy_end`) is unchanged.
- Only fund recommendations in `short_debt`, `arbitrage` and `arbitrage_plus_income` exist for
  debt (the ranking has no others); a held debt fund in any other subgroup is kept, not replaced.

### Definitions (per review, as of today)

- Short-term goals: `time_to_goal_months < HORIZON_BOUNDARY_MONTHS + months_to_fy_end`, ordered by
  `time_to_goal_months` ascending, ties by input order.
- `F_k` — goal k's future value (`amount_needed_fv`, falling back to `amount_needed`).
- `m_k` — `time_to_goal_months` (≥ 1).
- `H` — held short-term money (`short_term_holdings`); `None` = no holdings on file.
- `S` — the customer's stated monthly SIP (`monthly_sip`).
- `R` — corpus remaining after step 1 (step 2's existing `remaining_corpus` argument).
- `W` — `SIP_REVIEW_WINDOW_MONTHS = 6`, new constant in `asset_allocation_pydantic/tables.py`.

### Algorithm — `goal_waterfall()` (pure function, new, in step 2's module)

1. **Credit holdings.** Walk goals nearest first with `H_left = H or 0`:
   `from_holdings_k = min(H_left, F_k)`, `g_k = F_k − from_holdings_k`.
   `T_hold = Σ from_holdings_k`.
2. **Corpus needed.** With `G_k = g_1 + … + g_k`:
   `C_needed = max(0, max_k(G_k − S·m_k))` — the smallest amount that, placed now with the full SIP
   front-loaded, meets every goal date.
3. **Short-term target — round first, as step 2 does today.**
   `need = round_to_100(T_hold + C_needed)`; `T = min(need, R)`; `shortfall = need − T`;
   `C = T − T_hold` floored at 0. Step 2's `allocated_amount` becomes `T`; `total_goal_amount`
   becomes `round_to_100(ΣF)`; `future_investment` carries `shortfall`. Routing to one subgroup by
   tax rate is unchanged.
4. **SIP goal share for the next window.** `C_sip = 0` when `H` is `None` (no rebalance can move a
   corpus we cannot see), else `C`. `N = G_K − C_sip`.
   `x = max(N / W, max over goals with m_k < W of (G_k − C_sip) / m_k)`;
   `s = min(S, ceil_to_100(x))`, `0` when `N ≤ 0`. The whole SIP goes to goals while the
   remaining need is at least a window of SIP; the last window splits; the second term keeps a
   goal due inside the window on time. Long-term SIP = `S − s` (never negative).
5. **Per-goal attribution** (reporting only). Nearest first:
   `sip_room_k = S·m_k − Σ_{j<k} from_sip_j`; `from_sip_k = min(g_k, max(0, sip_room_k))`;
   the rest, `g_k − from_sip_k`, is met from `C` nearest first; anything left is that goal's
   shortfall. Invariants: parts sum to `F_k`; `Σ corpus part = C_needed` (verified over 20,000
   random cases during the audit).

### Worked examples (`W` = 6)

| Case | Inputs | Result |
|---|---|---|
| Holdings + SIP | H ₹9L; goals ₹3L@6m, ₹4L@10m, ₹2L@14m, ₹6L@20m; S ₹50k | First three from holdings; G4 gap ₹6L; `C` = 0; `s` = ₹50k (₹50k for 12 months); `T` = ₹9L |
| SIP too small | same, S ₹20k | `C` = ₹6L − ₹4L = ₹2L; `s` = ₹20k; `T` = ₹11L |
| Last window splits | one goal ₹5L@20m, H 0, S ₹50k | Review 1: `s` = ₹50k. Review 2 (₹3L held): `N` = ₹2L → `s` = ₹33,400, ₹16,600 to long-term |

### Edge cases

| Case | Behaviour |
|---|---|
| No short-term goals | `T` = 0, `s` = 0: whole SIP long-term (as today) |
| No SIP (`S` = 0) | `C_needed` = total gap after holdings — today's rule on future values, less existing debt |
| Holdings cover every goal | `T` = ΣF, `s` = 0; the excess joins long-term |
| Corpus can't cover the carve | `T` capped at `R`; `shortfall` reported through `future_investment`; `s` = `S` |
| Goal due inside the window | `s` raised to meet it; the rest of the window may overshoot; the next review sends the excess to long-term |
| No holdings on file (no-CAMS) | `H` = `None`: `s` assumes no corpus moves, so the SIP carries all it can; the long-term share uses the ₹1cr sizing rescue (see SIP flow) |
| Preference set | Step 2 already replaced by `_no_carveout_buckets` (unchanged); `goal_funding` = `None`; SIP all long-term = the stated split |
| Past-due goal | Dropped by `_map_goals` (unchanged); its leftover debt counts in `H` |

## Engine contracts

### Inputs — `asset_allocation_pydantic/models.py`

- `Goal.amount_needed_fv: Optional[float] = None` (> 0). Read by step 2 only; the long-term step
  keeps reading `amount_needed`, so far goals do not move.
- `AllocationInput.monthly_sip: float = 0.0` (≥ 0).
- `AllocationInput.short_term_holdings: Optional[float] = None` (≥ 0).

Names follow the allocation family (no `_inr` suffix, as `total_corpus` / `elss_corpus`); the
fresh-money engine keeps its own `_inr` style.

`PracticalAllocationInput` inherits all three, and `run_practical_allocation` already rebuilds
step 2's input from every `AllocationInput` field (`pipeline.py:1144-1147`).

**Defaults reproduce today exactly.** With `S` = 0, `H` = `None` and `F` = `amount_needed`:
`need = round_to_100(Σ amount_needed)`, `T = min(need, R)`, `shortfall = need − R` — today's
`allocated_amount` and `future_investment` (`step2_short_term.py:29-30,53`). The
`future_investment` message is unchanged when `S` = 0; its wording when `S` > 0 is in the chat spec.

### Outputs

New `GoalFunding` / `GoalFundingRow` in `asset_allocation_pydantic/models.py`, holding only what
cannot be derived elsewhere:

- `GoalFunding`: `allocated_amount` (T, as on `Step2Output`), `from_corpus` (C), `shortfall`,
  `monthly_sip` (S), `monthly_sip_to_goals` (s), `asset_subgroup` (step 2's routed subgroup — kept
  because the bucket carries no subgroup when `T` = 0, exactly when the SIP still needs it),
  `goals: list[GoalFundingRow]`.
- `GoalFundingRow`: `goal_name`, `time_to_goal_months`, `amount_needed_fv`, `from_holdings`,
  `from_sip`, `from_corpus`, `shortfall`. With `H` = `None` the corpus part is still attributed
  from `C`, so rows and totals agree.

Anything else chat needs (cost today, SIP months, status, long-term share) is derived in the chat
builder. Carried on `Step2Output.goal_funding: Optional[GoalFunding]`, surfaced as
`GoalAllocationOutput.goal_funding` (ideal, via step 7) and `PracticalAllocationOutput.goal_funding`
(`None` when a preference suspends step 2).

### Fresh money — `additional_investment`

**Goal money first** becomes the engine's only split, for SIP and lumpsum.

- `AdditionalInvestmentInput.goal_share_inr: float = 0.0` and `goal_subgroup: Optional[str] = None`.
- The goal share goes to `goal_subgroup` **by name** (never weighted by the short-term column, which
  is empty when `T` = 0). The rest follows the long-term column (SIP; `exclude_subgroups` respected,
  renormalised) or deficit-fill against each row's `total − short_term` (lumpsum).
- `target_bucket` = `short_term` when the goal share is at least half the amount, else `long_term`
  (the stored enum cannot take a new value; the SIP screen reads it).
- The legacy single-bucket mode loses its last caller and is deleted: `short_term_fulfilled`,
  `medium_term_fulfilled`, `select_target_bucket`, `compute_targets`, `dominant_bucket`. Persisted
  `request_input` is raw JSON and never re-validated, so old runs are unaffected.

### Rebalancing — never swap one debt fund for another

`Rebalancing/steps/step2b_suppress_debt_switch.py` already cancels matched debt sells against debt
buys, but only inside `DEBT_NETTING_POOL` = {`short_debt`, `arbitrage`, `arbitrage_plus_income`}
and never for off-list (rank 0) sells, so a customer's own liquid or gilt fund migrates into our
fund today. Change:

- `DEBT_NETTING_POOL` becomes every Debt-class subgroup the classifier produces.
- Off-list (rank 0) sells in that pool become eligible for netting.
- Unchanged: force-exits (`exit_flag`) still exit; a debt sell with no matching debt buy (excess
  debt) still goes to other asset classes.
- Bump Rebalancing `ENGINE_VERSION` (outputs change through the new targets too).

## App wiring

1. **Holdings — read once, in the practical input builder.** Holdings already arrive with the user
   (`user_context_loader.py`: portfolios → holdings → fund metadata). The builder classifies them
   with the pure snapshot code and sets `short_term_holdings` via `short_term_holdings_total()` over
   `SHORT_TERM_HOLDING_SUBGROUPS` (Debt-class subgroups minus `arbitrage_plus_income`, in
   `scheme_classification.py`); `None` when the customer has no holdings. The pure snapshot code
   (`HoldingsSnapshot`, `aggregate_holdings`, `snapshot_from_holdings`) moves from
   `additional_investment` to `app/domains/portfolio/services/holdings_snapshot.py`, because
   rebalancing imports the practical builder and must never import `additional_investment`.
   A caller may pass its own figure: rebalancing passes it from its ledger rows and lumpsum from its
   snapshot, so each flow uses one valuation. The ideal builder sets `0.0` (it starts from nothing).
2. **SIP amount.** Set once in `build_goal_allocation_input_for_user` from
   `starting_monthly_investment` (missing → 0); the practical builder inherits it. Only the SIP flow
   overrides it, with the amount being set up (one argument through `compute_practical_allocation_result`).
   SIP setup already writes that amount back to the profile (`ainv_engine/service.py:463-473`), so
   later rebalances use the same figure.
3. **Goal future value.** One public `custom_goal_fv(...)` in `cashflow_statement`, used by
   `engine/goals_table.py` (no behaviour change). `_map_goals` uses the cashflow builder's per-goal
   mapping, `map_custom_goal()` (present value, date, goal type, inflation override), rather than
   re-reading the ORM itself, so both value a goal identically. The cashflow engine runs on default
   `Assumptions()`, so no DB is needed.
4. **SIP flow** (`ainv_engine/service.py`, `input_builder.py`):
   - Fills `goal_share_inr` / `goal_subgroup` from the real-corpus run's `goal_funding` (`s`); `None`
     (preference) → share 0 → the whole SIP follows the stated split.
   - Deletes `_goal_funding_flags`, its cashflow-projection call, and the incomplete-profile gate
     that only existed for it (`service.py:287-299`).
   - The ₹1cr sizing rescue runs before the engine, when the SIP has a long-term share but the real
     run's long-term plan is under ₹10,000 (`_SIP_MIN_LONG_TERM_COLUMN_INR`) — today it fires only
     after a run with no buys. The floor also covers tiny corpora whose ₹100 rounding distorts the
     ratios (the case the preferences branch widened its trigger for). It supplies long-term ratios
     only; the goal share stays the real run's.
   - Bump `AINV_ENGINE_VERSION`.
5. **Lumpsum flow.** From the practical run at corpus + deploy (held short-term money passed from
   the snapshot): goal share = `min(deploy, C)` into the routed subgroup; the remainder deficit-fills
   the long-term plan (each row's `total − short_term`) against current holdings with only the
   goal-used held money (Σ `from_holdings`) removed, pro rata across the held short-term subgroups,
   so goal money is not funded twice while excess held debt still counts toward the emergency and
   long-term targets (decided 2026-09-29; preference customers keep full holdings). `deficit_facts`
   use the same map, with the routed subgroup's row also carrying `T` and the goal-used held money,
   so the Invest page's per-fund reasons match what was bought.
6. **Rebalancing flow.** Passes held short-term money from its ledger rows; the engine trades
   toward the new `T`.

No database migration: practical and allocation runs store their input and output as JSON, and
`target_bucket` keeps its existing values.

## Testing

- **`goal_waterfall`:** the three worked examples; every edge-case row; a property test that
  defaults reproduce today's step 2 (the golden fixtures have `goals=[]`, so they never exercise
  step 2 — this is the real check); the attribution invariants; `S − s ≥ 0` for any `S`.
- **Goldens:** `golden_practical_no_pref.json` and `golden_ideal_no_pref.json` re-pinned once; the
  diff must be only the added `goal_funding` key.
- **Fresh money:** goal share by name when the short-term column is empty; SIP remainder by
  long-term weights; lumpsum goal share first, remainder deficit-fill without double counting;
  `target_bucket` labelling; no goal share → the long-term plan.
- **Rebalancing netting:** an off-list liquid fund is kept when the plan buys debt; excess debt is
  still sold into other classes; force-exit unchanged; a parity test keeps `DEBT_NETTING_POOL` equal
  to the classifier's Debt subgroups.
- **App:** `short_term_holdings_for_user` on mixed holdings; `None` with no holdings; rebalancing and
  lumpsum pass their own figure; the SIP flow no longer calls the cashflow projection; the no-CAMS
  rescue keeps the real goal share; lumpsum `deficit_facts` match the buys.
- **Lifecycle sim (dev-only):** adapters pass `S`, `H`, FV and the new goal share from
  `goal_funding` (no engine logic in the sim). Compare before and after on the 5 profiles: corpus
  sold for goals per rebalance, debt↔equity round trips, debt-to-debt switches (expect 0), the month
  the SIP returns to long-term, every goal fully funded on its date.
- Record the full-suite baseline before starting and compare after; re-baseline app tests whose
  amounts move on purpose, stating why.

## Merge with `feat-central_investment_preference`

Expected conflicts: `ainv_engine/input_builder.py`, `ainv_engine/service.py`,
`rebal_engine/service.py`. Resolution:
- Keep the new fresh-money split. Drop that branch's `short_term_fulfilled` preference gate and its
  `goal_funding_flags_forced` extra — superseded, since a preference now yields a goal share of 0.
- That branch sets `practical_result` to the ₹1cr sizing run; take `goal_funding` from the
  real-corpus run instead.
- Its widened rescue trigger is preference-scoped (`_pref_shaped and grand_total < 10_000`); keep
  it alongside the new long-term-share trigger.
- Take its `classify_holding` AMFI-label fix; it improves `H`.
- Both branches have bumped `AINV_ENGINE_VERSION` independently; set a fresh value after the merge.

## Not in scope

- Facts packs, prompts, per-goal rationale wording, the SIP-amount what-if and the Logics doc
  refresh — the chat spec.
- The 6-month review job — a separate workflow. Until it exists, a SIP keeps the split it was set
  up with. When built, it must set the CAS scope (`app/core/cas_scope.py`) so `H` does not add up
  old statements.
- The Goal Planning screen's own "funded" view.
- The emergency-fund carve (off in production).
- Frontend changes.
