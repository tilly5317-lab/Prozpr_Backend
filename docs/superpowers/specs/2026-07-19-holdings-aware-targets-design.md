# Holdings-aware targets — design

**Date:** 2026-07-19
**Status:** designed, **not implemented**. Prerequisites in §8 must land first.
**Supersedes:** [`2026-07-18-rank-downgrade-suppression-design.md`](2026-07-18-rank-downgrade-suppression-design.md)
— that note proposed a *no-downgrade* rule implemented as an intent-suppression step, sized on a
fixture we later found seeded MF holdings at up to 2× the correct book. Different rule, different
place, superseded numbers. Nothing in it should be built.
**Does NOT supersede:** [`2026-07-18-debt-switch-netting-design.md`](2026-07-18-debt-switch-netting-design.md).
Step2b stays — see §7, which is the section to read if you think these changes make it redundant.

---

## 1. The problem

The engine sells a fund the customer already holds to buy a similar one in the same subgroup.
Two distinct causes, created in different steps:

**TYPE 1 — cap spill.** `_assign_targets_to_rank1` (`pipeline.py:45-87`) puts a subgroup's *whole*
target on the rank-1 row. `step1_cap_and_spill` clips it at the per-fund cap and cascades the
overflow to rank-2, which becomes a buy funded by the rank-1 sell.

> Measured, 5-profile sweep, **representative fixture**: **₹81,100 — 0.8% of all selling.**
>
> An earlier figure of ₹7,86,879 / 31.5% was measured before `synth_holdings` was rewritten. That
> fixture seeded 100% of every subgroup into its rank-1 fund, which *guarantees* a cap breach —
> so Type 1 was largely manufactured by the test data. On a realistic rank distribution it is
> **small**. Change 1 is now justified as the mechanical enabler for Change 2 (§2), not as a
> benefit in its own right.

**TYPE 2 — rank-1-only targets.** `input_builder.py:272` assigns
`target_amount_pre_cap = rank1_target if rr.rank == 1 else Decimal(0)`. A customer holding the
rank-3 fund therefore has target 0, `diff = -(holding)`, and it is sold to fund the rank-1 buy.

> **Measured on real portfolios (§9): 69.4% of held ranked rows are rank ≥ 2 — 68.2% of held value,
> ₹6.21 crore.** Now measurable in the sim too, since `synth_holdings` was rewritten to that
> distribution. **This is the change that matters.**
>
> ### Two figures, two questions — do not conflate them
>
> | | measures | value | % of selling |
> |---|---|---|---|
> | **Paired churn** *(lower bound)* | a rank ≥ 2 holding sold **and** rank-1 bought in the *same* subgroup — money going in a circle | **₹16,54,877** | **16.7%** |
> | **All non-rank-1 selling** *(upper bound)* | every sell from a rank ≥ 2 holding, however the proceeds are used | **₹73,76,941** | **74.9%** |
>
> The gap is selling that funds buys in *other* subgroups — genuine rebalancing, not churn.
>
> **Change 2 prevents something between the two.** It does not suppress pairs; it gives a held fund a
> target equal to its holding, so the sell never arises regardless of where the money would have
> gone. But where a subgroup's total target is genuinely below what is held, the oversubscription
> trim (§4) still cuts floors and some selling survives — correctly.
>
> **The true figure is only knowable after implementation.** Quote 16.7% when you need a number you
> can defend; quote 74.9% only as a ceiling, clearly labelled. Do not quote the midpoint.
>
> Paired examples, all delta 1, all suppressed by the band:
> ```
> Harpreet   multi_asset        rank2 -> rank1   ₹823,719
> Neha       multi_asset        rank2 -> rank1   ₹578,916
> Harpreet   gold_commodities   rank2 -> rank1   ₹ 86,177
> Harpreet   low_beta_equities  rank2 -> rank1   ₹ 39,200
> Mohammed   us_equities        rank2 -> rank1   ₹ 29,200
> ```

---

## 2. The two changes

**CHANGE 1 — the per-fund cap stops clipping below a protected holding.**
`step1_cap_and_spill.py`: `effective_cap = max(cap_amount, r.protected_floor_inr)`.
The cap governs where *new* money is deployed, not what the customer is forced to sell.

**CHANGE 2 — held recommended funds get a target instead of 0**, with a rank band deciding
protect-vs-upgrade. Implemented as *reserve-then-residual* in `pipeline.py` (§4).

They are **coupled**. Change 2 sets a floor; Change 1 is what stops step 1 clipping straight back
through it. Change 2 alone is actively harmful — see §5.

---

## 3. Decisions on record (product owner, 2026-07-19)

| # | Decision | Consequence accepted |
|---|---|---|
| 1 | **Cap protection applies to every subgroup**, not debt-only | A customer holding 22% of corpus in one large-cap fund (10% cap) is never trimmed. The debt rationale — "all debt funds deliver similar returns" — does not transfer to equity; this was chosen anyway, for consistency and because the band cannot work on equity without it |
| 2 | **Protection is permanent. No hard ceiling.** | The floor is *current market value*, so it ratchets upward as the fund appreciates and never decays. Concentration is bounded only by dilution from new allocation |
| 3 | **No coupling to `additional_investment`** | Rebalancing moves existing corpus; additional investment deploys new money. They stay independent decisions. Consequence: rebalancing's residual dilutes rank-1 while SIP keeps buying rank-1 (`selection.py:6` — *"the customer's existing holdings are not consulted"*), so the two can pull opposite ways in the same month |
| 4 | **Band is EXCLUSIVE: protect when `delta < 5`, sell at `delta >= 5`** | A rank-6 holding against a rank-1 best is delta 5 → **sells**. Under exclusive, `gold_commodities`, `multi_asset` and `short_debt` can still produce sells; the other 7 live subgroups are fully protected |
| 5 | **Band applies to `multi_asset` too**, and the `multi_asset` ladder will be made homogeneous | Today that ladder mixes Aggressive Hybrid ×4, Dynamic AA/BAF ×3, Flexi Cap ×2, Multi Asset ×1 — rank distance is *not* similarity distance, and rank-1→rank-2 is a hybrid→pure-equity swap. **The band's premise here depends on that cleanup landing.** Until it does, we suppress a swap the engine calls churn and the real world might not |
| 6 | **No per-subgroup band override for `gold_commodities`** | Its 10 funds are interchangeable gold ETFs, so a rank-6→rank-1 swap is near-pure churn and will still occur. Accepted because the sleeve may later hold silver funds, at which point rank distance means something again |
| 7 | ~~**Drop the `Target` column from the chat summary table**~~ **NOT IMPLEMENTED — premise falsified, 2026-07-19. See below.** | `formatter.py:141` becomes `\| Category \| Current \| Plan \|`. Sourcing Target from the constrained plan would duplicate the Plan column. Consequence: a customer holding 86% of corpus in one fund sees `Current ₹82L / Plan ₹82L` and **has no way to tell they are off-plan** — the deviation becomes invisible rather than explained |

**Decision 7 was not implemented. Its premise does not hold.** It assumed `Target` is sourced from
the constrained plan and therefore duplicates `Plan`. It is not: `_bucket_target` reads
`SubgroupSummary.goal_target_inr`, which `step6:229-239` sums from `target_amount_pre_cap` — the
**pre-cap ideal**, not the plan. Measured after Change 2 on the 5-profile harness: **27 of 31 buckets
have Target ≠ Plan**, several by a wide margin (Mohammed / Large Cap: current ₹1,92,592, target
₹54,100, plan ₹77,036). The column is carrying the off-plan signal the decision's own "Consequence"
clause worried about losing.

Change 2 does not change this. `target_amount_pre_cap` becomes multi-row per subgroup, but the
per-subgroup sum is still `T`: floors are trimmed so `Σfloor <= T`, hence
`Σfloor + max(T - Σfloor, 0) = T`. `goal_target_inr` means exactly what it meant before — verified
to the rupee across the kill-switch toggle (§10).

Dropping the column is still available as a pure product call — "customers shouldn't see an ideal
they can't reach" — but it must be argued on that basis, not on duplication. **Deferred pending the
product owner's call.**

---

## 4. CHANGE 2 — the algorithm

Lands in `pipeline.py`; rename `_assign_targets_to_rank1` → `_assign_subgroup_targets`.

```
CONSTANTS  RANK_PROTECT_BAND = 5      (EXCLUSIVE: protected iff delta < 5)
           EXIT_FLOOR_RATING = 5, FORCE_EXIT_RANK = 9999

for each subgroup sg in practical.aggregated_subgroups, not in _FROZEN_SUBGROUPS:

    T := practical total for sg
    T := max(T - Σ st_value_inr of rank-0 rows in sg, 0)    # NEUTRAL ST offset FIRST —
                                                            # floors carve out of the REDUCED total
    ranked := [r in sg where 1 <= r.rank < FORCE_EXIT_RANK]  # excludes rank 0 and force-exit
    sort ranked by (rank, isin)                              # isin tie-break: ranks are not
    if ranked empty: continue                                # uniqueness-validated anywhere
    best := min(r.rank for r in ranked)                      # do NOT hardcode 1

    # --- floors ---
    for r in ranked:
        protected := r.present_allocation_inr > 0
                     and (r.rank - best) < RANK_PROTECT_BAND
                     and r.fund_rating >= EXIT_FLOOR_RATING
        floor[r] := floor_to_step(r.present_allocation_inr, rounding_step) if protected else 0

    # --- oversubscription trim, worst-rank-first ---
    excess := Σ floor - T
    for r in ranked sorted by (-rank, isin):
        if excess <= 0: break
        cut := min(floor[r], excess); floor[r] -= cut; excess -= cut

    # --- residual ---
    R := max(T - Σ floor, 0)
    target_amount_pre_cap[rank-1 row]  := R + floor[rank-1 row]
    target_amount_pre_cap[other ranked] := floor[r]
    protected_floor_inr[r]              := floor[r]     for all ranked r

# --- CHANGE 1, in step1_cap_and_spill ---
effective_cap := max(cap_amount, r.protected_floor_inr)
alloc_3_raw   := min(with_spill, effective_cap)
```

**Why `floor_to_step`, not the raw holding.** `step1:98` rounds targets to the nearest ₹100, which
leaves a phantom `diff` of up to ₹50 on a protected row — measured **4949/5000 trials**. Flooring
the floor to the rounding step removes it: **0/5000**.

**Why the residual goes to rank-1 and then spills.** Rank-1 receives `R + its own floor`; step 1
clips it at `max(cap, floor)` and cascades `R` down the ladder. This is what makes new allocation
land on rank-2, rank-3 — the dilution mechanism in decision 3 — and it reuses step 1's existing
spill rather than reimplementing it.

**Worked example (Lakshmi, `short_debt`):**
```
T = ₹93,50,000        rank-1 holds ₹81,97,429, ranks 2-4 hold nothing
floors: rank-1 ₹81,97,400 (protected, delta 0), ranks 2-4 = 0
Σ floor < T → no trim.  R = ₹11,52,571
rank-1 target = R + floor = ₹93,50,000
step1: effective_cap = max(₹28,50,000, ₹81,97,400) = ₹81,97,400
       → clip to floor, spill ₹11,52,571 to rank-2
result: rank-1 diff = 0 (no sell), rank-2 diff = +₹11,52,571 (genuine buy)
```

### Carve-outs

| Case | Behaviour | Why |
|---|---|---|
| `rank == 0` (off-list) | **Never protected** — structurally excluded from `ranked` | A naive predicate computes `\|0-1\| = 1 < 5` and protects them, killing off-list migration. Excluding by list membership makes that unreachable. Also preserves the LT-only sell (`step1:114-126`) that keeps recently-bought units out of STCG — relabelling these as e.g. rank 50 would lose that |
| `rank == FORCE_EXIT_RANK` | Exits fully | Doubly guaranteed: delta 9998 fails the band, and rank 9999 is excluded at `step1:54` |
| `fund_rating < EXIT_FLOOR_RATING` | **Not protected**, even at rank 1 | `exit_flag` is computed in step2 (`step2:46`), *after* target assignment, so the pipeline must replicate the rating test. Otherwise a protected+exit row emits contradictory output: step4 sells `present` in full while step6 reports `goal_target = present`. Dead today (`input_builder.py:51` hardcodes rating 10) but one config change from live |
| Customer holds nothing in sg | All floors 0, `R = T` → **byte-identical to today** | Provable no-op |
| Subgroup target is 0 | Subgroup absent from the map → no floors → full exit still possible | Make this an explicit test, not an implicit consequence |
| Holdings > T | Worst-rank-first trim, `R = 0` | `total_corpus` is Σ present (`input_builder.py:408-412`), so every under-weight subgroup is matched by an over-weight one. Without the trim, over-weight subgroups could never de-allocate |
| Duplicate ranks | `isin` tie-break in **three** sort sites | Zero duplicates in either CSV today, but the residual must land on exactly one row |

---

## 5. Why Change 2 must never ship alone

Monte-Carlo over 3,000 randomised subgroups (2-6 ranks, random corpus/target/holdings):

| variant | result |
|---|---|
| Naive Change 2 (rank-1 keeps full `T`) | **conservation broken in 1,906 / 3,000 trials, worst gap ₹2,13,00,000** |
| Change 2 alone | **creates new intra-subgroup churn in 1,084 / 3,000** |
| Reserve-then-residual (§4) | **0 / 3,000 conservation gap** |
| Change 1 + Change 2 together | **0 / 3,000 intra-subgroup churn** |

Shipping Change 2 first would trade Type-2 churn for new Type-1 churn a third of the time, and make
both changes unattributable.

**Two things verified that remove earlier concerns:**
- **There is no `sum(final_target) == total_corpus` invariant** anywhere in the repo. Step 1 books
  every overflow rupee to `spill_in[i+1]` or `unrebalanced_total` (`step1:78-96`), so conservation
  is per-subgroup and structural regardless of the cap value. The `min(present, subgroup_target)`
  clamp considered earlier is **not needed** — `with_spill` is already bounded by the subgroup total.
- **`max_pct` is write-only.** Only `rebalancing_persist_service.py:165` (write) and
  `rebalancing_fund_row.py:71` (column) touch it — no app, formatter or read service consumes it.
  Safe to redefine as the *effective* cap, which matches the existing precedent at `step1:66-68`
  and `Rebalancing/CLAUDE.md:21` ("max_pct reports the EFFECTIVE cap when the floor wins").

---

## 6. Change 1 — precise change

```python
# step1_cap_and_spill.py, after line 72
effective_cap = max(cap_amount, r.protected_floor_inr)
max_pct       = _pct_of_corpus(effective_cap, corpus)   # :73  cap_amount -> effective_cap
own_capped    = min(r.target_amount_pre_cap, effective_cap)              # :75
if with_spill > effective_cap:                                           # :78
    alloc_3_raw = effective_cap                                          # :79
    overflow    = with_spill - effective_cap                             # :80
```

**Gate on `protected_floor_inr`, not on `present_allocation_inr`.** Using raw `present` raises the
cap for *every* held row — including a rank-8 holding the band does not protect, letting it absorb
spill above its cap purely because the customer owns it.

**Also needed:** `models.py` — add `protected_floor_inr: Decimal = Field(default=Decimal(0), ge=0)`
to `FundRowInput`. Defaulted, so the `FundRowAfterStepN` chain propagates it for free and no fixture
breaks. The comment at `models.py:56-57` ("only rank-1 of each subgroup carries amount") becomes
false and must be rewritten — it is stated as a contract.

### Configuration and operational knobs

| knob | default | env override | home |
|---|---|---|---|
| `RANK_PROTECT_BAND` | `5` | `REBAL_RANK_PROTECT_BAND` | `config.py`, Bucket A, beside the cap thresholds |
| `HOLDINGS_AWARE_TARGETS_ENABLED` | `True` | `REBAL_HOLDINGS_AWARE_TARGETS` | `config.py` |

**Ship the kill-switch.** It is not optional ceremony — the equivalent seam on the debt work
(`REBAL_DEBT_SWITCH_NETTING`) is what made a clean A/B possible on an identical fixture, and that
A/B caught a conservation break and a trade-count regression that reasoning had missed. These
changes touch every subgroup and are harder to reason about than the debt pool was.

**`ENGINE_VERSION` → `1.3.0`** (`config.py`), with a changelog line. 1.2.0 is the debt netting.

**Stamp both knobs into `KnobSnapshot`** (`models.py`, populated at `step6:50-65`) so a persisted
run is explainable from its own snapshot. Note the debt work shipped **without** `debt_netting_mode`
in the snapshot — two runs from different distribution modes are currently indistinguishable after
the fact. Do not repeat that; fix it in the same pass if convenient.

**`protected_floor_inr` is NOT persisted.** It is an input to step 1, and `final_target_amount`
(already a column, `rebalancing_fund_row.py:81`) captures the outcome. No DB column, no migration —
same call as `netted_target_adjustment_inr` on the debt work, and it keeps this change clear of the
broken alembic path.

---

## 7. step2b stays — and here is the evidence

An earlier scoping pass concluded step2b becomes redundant because it "fires zero times" after
Change 1. **That was measured on the single-rebalance sweep, where it cannot be true.**

Cross-subgroup wrapper switching happens when the allocation engine *re-routes* between debt
wrappers as tax rate and goal tenure drift. At month 0 there is no drift, so the sweep structurally
cannot produce it. Measured on the **24-month lifecycle** with netting off:

```
CROSS-SUBGROUP DEBT SWITCHES THAT SURVIVE CHANGE 1   (representative fixture)
  Harpreet   reb#3  arbitrage_plus_income -> arbitrage             ₹2,173,800
  Neha       reb#3  arbitrage_plus_income -> arbitrage             ₹1,644,400
  Harpreet   reb#4  arbitrage_plus_income -> arbitrage             ₹  842,200
  Neha       reb#4  arbitrage_plus_income -> arbitrage             ₹  339,874
  Neha       reb#0  arbitrage_plus_income -> arbitrage             ₹  303,437
  Neha       reb#0  arbitrage_plus_income -> short_debt            ₹  228,200
  Neha       reb#2  arbitrage             -> arbitrage_plus_income ₹  154,064
  Neha       reb#1  arbitrage             -> arbitrage_plus_income ₹  133,494
  (+ 16 smaller crossings across Mohammed, Neha and Harpreet)
  TOTAL                                                           ₹6,995,614
```

Every one is a **de-allocation** sell (`target_amount_pre_cap < present`) — the subgroup's own
target fell, no cap involved. Change 1 raises a ceiling *within* a subgroup; Change 2's band
compares ranks *within* a subgroup. Neither touches a subgroup-level target move.

**And it oscillates.** Neha goes arbitrage→a_p_i at reb#1/#2, then a_p_i→arbitrage at reb#3/#4.
Harpreet runs short_debt→a_p_i for three rebalances, then reverses into arbitrage. The routing flips
as tenure and tax rate cross the thresholds, and the customer pays tax each way — Neha alone churns
₹25.7L across five rebalances between wrappers holding near-identical assets. That is a stronger
justification for the debt equivalence than the original m18 example: it is a pendulum, not a
one-off.

Note this figure **grew** when the fixture was made representative (₹57,58,423 → ₹6,995,614), the
opposite of Type 1. Wrapper switching is driven by allocation re-routing over time, not by holdings
concentration, so a realistic starting distribution does not damp it.

**Consequence: the lifecycle sim is the required harness for anything debt-related.** The
single-rebalance sweep cannot see this class of behaviour at all.

**step2b's two known defects become must-fix**, since it is live code doing real work:
1. `_cap_spill_buy_reductions` (`step2b:93`) sorts buys by `(rank, isin)` across the *whole pooled
   debt set*, so surviving demand crosses subgroup lines — measured **₹4,565** leaking between
   `arbitrage_plus_income` and `short_debt`, making step6's displayed target disagree with the
   practical engine.
2. step2b **over-cancels**: it reduced an arbitrage buy by **₹2,00,000** that was funded by an
   *equity* sell, not a debt sell.

**One claim investigated and refuted — do not re-raise it.** A reviewer argued that `step2b:99`
(`grant = min(remaining, cap_amount, r.diff)`) re-clips protected floors and silently undoes
Change 1. It cannot. Under Change 1, `final_target = min(with_spill, max(cap, floor))`, so if
`floor > cap` then `final_target <= floor = present` and therefore `diff <= 0` — while step2b's buy
list requires `diff > 0` (`step2b:126-130`). A protected over-cap row can never be a buy, so that
line never sees one. Measured: **5,093 protected rows, 521 became buys, 0 were over the nominal
cap.** Extracting the shared cap helper is still worth doing as hygiene (prerequisite 3), but it is
not gating.

---

## 8. Blast radius and prerequisites

### Prerequisites (no behaviour change; land separately so the diff is readable)

1. ~~Build a rank-2/rank-5 holdings fixture.~~ **DONE 2026-07-19.** `synth_holdings` now seeds the
   production rank distribution (`HELD_RANK_CYCLE`, 30% rank-1 / 60% rank-2 / 10% rank-3, plus a
   second holding in ~1 of 8 subgroups) instead of 100% into rank-1, and takes the full `ranking`
   ladder rather than the rank-1 projection. Three call sites updated (`runner.py`,
   `test_5_profile_smoke.py`, `engines.py`); 209 tests pass; both harnesses re-baselined.
   **Every number in this spec and in the debt-netting note predates that rewrite unless dated
   2026-07-19.**

   **Deep-rank coverage holding — added deliberately, product owner's call, 2026-07-19.**
   `DEEP_RANK = 7` at 15% of the subgroup target, placed in the first ladder deep enough (only
   `multi_asset` and `gold_commodities` qualify). Lands for Mohammed, Neha and Harpreet; Aarav and
   Lakshmi have no eligible subgroup.

   I had argued against this — real portfolios show nothing beyond rank 3, so it makes the sim
   unrepresentative. The counter-argument won: **a band that never sells anything is
   indistinguishable from a band that is broken.** With every holding inside the band, the sim can
   only ever demonstrate the protect branch, and Change 2 would ship without end-to-end evidence
   that the sell branch fires at all.

   Cost, measured: **₹309,911 — 3.1% of selling.** Separable, so headline figures stay usable
   (debt-netting baseline moved −65.8% → −64.6%). Quote the realistic figure and exclude rank ≥ 7
   when reporting Type-2 impact.

   Caveat: **it proves nothing until Change 2 exists.** Today a rank-7 holding is sold for the same
   reason a rank-2 one is — target 0 — so it is currently indistinguishable from the rest. It only
   becomes a discriminator once the band is live and rank-2/3 stop selling while rank-7 continues.
2. ~~Fix the `pipeline.py:75` / `step6:226` mismatch.~~ **DOWNGRADED to documented, 2026-07-19 —
   investigated, not a blocker.** It is reachable (3 of 5 profiles; Lakshmi ₹4,61,028), but every
   instance has `T = 0`: the plan wants nothing in the subgroup, the customer holds short-term-locked
   units there, and `goal_target_inr` reports what they will actually keep. That is the same
   semantic choice as decision 7 — Target reflects the constrained plan, not the unreachable ideal —
   so "fixing" it would contradict a decision already made. Change 2 handles `T = 0` correctly by
   construction: the oversubscription pass trims floors to zero, `R = 0`, ranked holdings sell, and
   NEUTRAL keeps its locked ST. Leave it.
3. ~~Extract the cap formula.~~ **DONE 2026-07-19.** `tables.effective_cap_for(subgroup, corpus)` is
   the single source of truth; `step1` and both `step2b` sites call it. Previously inlined in three
   places, which is how a cap change would have applied to some and not others.
4. ~~Add `isin` tie-breaks.~~ **DONE 2026-07-19** at `step1:55` (`key=lambda r: (r.rank, r.isin)`).
   `step2b:92` already had one. The spill cascade no longer depends on stable-sort order to decide
   which row receives the overflow.

### Change 1

**Tests: 5 fail, none of them step 1 tests.** `test_step1_caps.py` (4) and `test_step1_cap_floor.py`
(3) pass unchanged — every fixture has `present_allocation_inr = 0` on ranked rows. The 5 failures
are in `test_step2b_cap_spill_mode.py` (2) and `test_step2b_debt_netting.py` (3), whose fixtures are
Type-1 cap-spill scenarios Change 1 dissolves. **Re-base them on cross-subgroup fixtures; do not
delete them** — they still guard the wrapper-switch case in §7.

**Persisted meaning shifts:** `max_pct` (policy → effective cap; write-only).
`target_own_capped_pct` collapses onto `target_pre_cap_pct` for protected rows.
`unrebalanced_remainder_inr` shrinks; `UNREBALANCED_REMAINDER` warning volume drops.

**Customer-visible:** trade count and realised CGT both fall. The `Target` column disappears per
decision 7. The Invest-page headline is unaffected — `rebalancing_summary.py:73-77` deliberately
uses `suggested_final_holding_inr`.

### Change 2

**Tests: zero fail** — see prerequisite 1.

`target_amount_pre_cap` becomes multi-row per subgroup. `goal_target_inr` needs no code change
(`step6:226-232` already sums across all rows) but is the exact surface where a conservation break
would become a customer-visible wrong number.

**App layer:** a fully-protected subgroup can emit **zero BUY trades**.
`rebalancing_read_service.py:60-66` returns `None` when the latest run has no BUY trades, so the
`additional_investment` SIP path silently degrades to its rank-1 fallback for that subgroup.
Graceful, but the SIP fund map narrows.

---

## 9. Evidence — measured on real portfolios, 2026-07-19

The superseded note recommended measuring rank concentration in real CAS-ingested portfolios before
building. **Done.** Dev RDS: 63 rebalancing runs, 34 distinct users, 13,850 MF transactions over
three months — genuine usage, not a seeding event. 562 fund rows with holdings, 111 of them ranked.

### Type 2 is the majority case in production — the sim had it exactly backwards

| rank held | rows | value | share |
|---|---|---|---|
| rank 1 | 34 | ₹2,90,04,971 | 30.6% |
| **rank 2** | **64** | **₹5,88,61,410** | **57.7%** |
| rank 3 | 13 | ₹32,50,707 | 11.7% |

**69.4% of held ranked rows are rank ≥ 2 — ₹6,21,12,117 of ₹9,11,17,089, 68.2% of value.** Every
one of those gets `target_amount_pre_cap = 0` today and is sold to fund a rank-1 buy.

`synth_holdings` seeds **100% into rank-1**. Production is close to the inverse. Any conclusion
about Type 2 drawn from the sim was drawn from a fixture that structurally could not contain it —
**Change 2 is now the better-evidenced of the two changes**, which is the opposite of where this
started.

### Change 1 is real but smaller than the sim implied

29 of 111 ranked held rows are over their cap (**26.1%**), **₹33,99,943 over** across ₹1,96,11,673
of holdings. By subgroup: `high_beta_equities` 13 rows / ₹18,28,630 over, `multi_asset` 15 rows /
₹15,11,425, `tax_efficient_equities` 1 row / ₹59,888.

Note the largest exposure is **pure single-style equity**, which is exactly where decision 1's
rationale is weakest. Worst real case: **`multi_asset` rank-2 at 75.9% of corpus against a 20%
cap** — under decisions 1 and 2 that customer is protected permanently, with no ceiling and no trim.

*(A raw figure of 31.5% over 562 rows includes rank-0 off-list holdings — `others_fofs` at 100% of
corpus, `sector_equities` at 82% — which the band never protects and Change 1 never touches. Use
26.1% / ₹34L, not 31.5%.)*

### The band does not discriminate on today's holdings

```
delta 0:  34 holdings (30.6%)  -> PROTECTED
delta 1:  64 holdings (57.7%)  -> PROTECTED
delta 2:  13 holdings (11.7%)  -> PROTECTED
```

**No real holding sits at delta >= 3.** Under BAND = 5 every production holding is protected, the
band never permits an upgrade, and **any band value >= 3 is byte-identical today.** The choice of 5
is currently unobservable; it only starts to matter once the ladders deepen (decision 5) or
customers hold more dispersed positions. Implement the band as specified, but do not expect the
constant to change behaviour yet — and do not tune it against current data, because current data
cannot distinguish the options.

### Customers hold one fund per subgroup

85 of 98 subgroup-occurrences hold exactly one fund; 13 hold two; none hold three or more. So the
band rarely arbitrates *between* held funds — it almost always decides whether to migrate a single
holding upward. At delta 1 (rank-2 held, rank-1 best) it protects, and **57.7% of all holdings sit
exactly there.**

---

## 10. Sequencing

**Step 0** — prerequisites (§8).

**Step 1 — Change 1, scoped to `protected_floor_inr`.**
Small on its own — Type-1 churn is ₹81,100, 0.8% of selling (§1). **It ships as the mechanical
enabler for Change 2, not for its own benefit**: without it, step 1 clips straight back through the
floors Change 2 sets and nothing is protected. Expect a barely-visible delta; that is the correct
outcome, not a failed change. Re-base the 5 step2b tests in the same PR. **Re-run the lifecycle sim,
not just the sweep** (§7).

**Step 2 — Change 2, reserve-then-residual, pipeline-side. This is the change that pays.**
Expected: Type-2 churn ₹16,54,877 → ~₹0, ~16.7% of remaining selling removed. With Change 1 already
in, the delta is pure Type-2 reduction with zero new Type-1 churn (0/3000, §5).

**Never Change 2 alone.**

### Both shipped 2026-07-19 — measured

`ENGINE_VERSION = 1.3.0`. 347 tests pass (Rebalancing + practical_asset_allocation +
additional_investment + app-side rebalancing). Measured across the 5-profile harness by toggling
`REBAL_HOLDINGS_AWARE_TARGETS`, so both columns come from one fixture:

| | band off | band on | |
|---|---:|---:|---|
| total selling | ₹34,88,801 | ₹19,37,267 | −44.5% |
| **paired same-subgroup churn** | **₹18,74,448** | **₹2,64,637** | **−85.9%** |
| sell at rank 1 | ₹3,29,870 | ₹2,22,270 | −32.6% |
| sell at rank 2 | ₹16,43,752 | ₹2,38,491 | −85.5% |
| sell at rank 3 | ₹57,347 | ₹18,674 | −67.4% |
| sell at rank 7 (outside band) | ₹3,09,911 | ₹3,09,911 | **unchanged** |
| sell at rank 0 (off-list) | ₹11,47,921 | ₹11,47,921 | **unchanged** |

The last two rows are the point. **All ₹2,64,637 of surviving paired churn is rank-7 → rank-1** —
Neha ₹99,978 and Harpreet ₹1,64,659, both `multi_asset`. Zero rank-2/3 pairing remains. The deep-rank
holding did the job it was added for (§8 prerequisite 1): it is now the discriminator proving the
sell branch still fires, which is why the estimate of "Type-2 → ~₹0" reads as ₹2.6L rather than nil.

Expectation vs outcome: Type-2 was projected at ₹16,54,877 removed; ₹16,09,838 of rank-2/3 selling
actually went, and total selling fell by more than that (₹15,51,534) because Change 1 also removed
the rank-1 cap-driven trims.

**Conservation verified on `goal_target_inr`** — the surface §8 flagged as where a break would
become a customer-visible wrong number. Per-profile debt-pool totals and every non-debt subgroup are
identical to the rupee across the toggle. Eight subgroups shift *within* the debt pool (e.g. Aarav
`arbitrage_plus_income` ₹2,69,348 → ₹3,00,000 against `short_debt` ₹1,80,652 → ₹1,50,000, netting
zero): with less debt selling to cancel, step2b makes fewer `netted_target_adjustment_inr` moves and
`goal_target_inr` lands on the practical engine's clean subgroup totals instead of netting-adjusted
ones. That is a cleaner number, not a drifted one.

> **Note the priority inverted twice.** This spec originally sequenced Change 1 first because its
> problem was measured and Change 2's was not. Both halves of that turned out to be fixture
> artifacts: Type 1 shrank 31.5% → 0.8% once `synth_holdings` stopped seeding 100% into rank-1, and
> Type 2 went from invisible to 16.7%. The *order* is unchanged — Change 1 is still mechanically
> required first — but it is now an enabler, not the payoff.

---

## 11. Verification

Nothing here is optional. Two conclusions in this work were already overturned by measurement after
looking obviously true.

**Unit — the band boundary.** The exclusive boundary is the single most likely thing to be
implemented backwards. Assert *both* sides explicitly: a rank-5 holding against a rank-1 best
(delta 4) is **protected**; a rank-6 holding (delta 5) **sells**.

**Unit — every carve-out in §4**, each with its own test: rank-0 off-list still migrates;
`FORCE_EXIT_RANK` still exits; a sub-floor rating is not protected even at rank 1; a subgroup where
nothing is held is byte-identical to today; the oversubscription trim cuts worst-rank-first.

**Unit — conservation.** `Σ target_amount_pre_cap == T` per subgroup after floors and residual, and
`diff == final_target_amount − present_allocation_inr` on every row. The Monte-Carlo in §5 is the
model for this — a handful of hand-picked fixtures will not find a 1-in-3 failure rate.

**Unit — the phantom diff.** A protected row must end at `diff == 0`, not `−29`. This is what
`floor_to_step` on the floor exists to prevent, and it fired in 4949/5000 randomised trials before
the fix.

**Wiring.** A test that fails if either change is reverted or unwired. On the debt work, sabotaging
the pipeline left 13 of 14 tests passing — every test drove the step directly and nothing noticed
the orchestrator no longer called it.

**Integration — the rank-2/rank-5 fixture** (prerequisite 1). Without it Change 2 has no test that
can observe it working *or* breaking.

**Measurement — the lifecycle sim, not just the sweep.** §7 is the standing reason. Report per
profile: total sell, trade count, realised CGT, and cross-subgroup debt switching, before and after,
via the kill-switch on an identical fixture.

**The acceptance test for the band is a *contrast*, not a total.** After Change 2, in the same run:
rank-2 and rank-3 holdings must stop selling, **and the rank-7 deep holding must keep selling.** If
both stop, the band is not discriminating — it has degenerated into "never trade", which is
indistinguishable from an implementation that ignores rank entirely. That contrast is the only thing
that demonstrates the rule works rather than merely blocks everything, and it is why the deep
holding was added to the fixture.

**Exclude rank ≥ 7 from reported Type-2 impact** (₹309,911, 3.1% — §8 prerequisite 1). It is
coverage, not signal.

---

## 12. Open items

- **`multi_asset` ladder homogenisation** (decision 5) — product action on the ranking CSV. The
  band's premise in that subgroup depends on it.
- ~~Real-portfolio rank-concentration measurement — should gate both changes.~~ **Done 2026-07-19,
  results in §9.** It reversed the priority: Change 2 is the better-evidenced change, not the
  speculative one.
- **step2b's two defects** (§7) — must-fix, not tolerate.
- **Absolute band vs proportional.** `RANK_PROTECT_BAND = 5` means something different on a 6-fund
  ladder than on a 30-fund one, and decision 5 commits to *deepening* the ladders. The day a CSV
  grows, the rule silently loosens without anyone changing a constant. Worth deciding whether the
  band should instead be a fraction of ladder depth (e.g. protect the top half). **Not put to the
  product owner yet.**
- **Warning asymmetry.** A debt wrapper switch produces a customer-facing explanation
  (`DEBT_SWITCH_SUPPRESSED`, rendered as a heads-up bullet at `formatter.py:251`). Concentration
  left behind by Change 1 produces **nothing** — decision 7 removed the column that would have
  revealed it, and step 1 has no warning path. So a customer sitting at 86% of corpus in one fund is
  told about the debt case and not about theirs. Coherent with decision 7, but the asymmetry is
  accidental rather than chosen, and worth a deliberate look once these ship.
- **No live mechanism exits a degraded ranked fund inside the band.** Rating-based exit is dead
  (`input_builder.py:51` hardcodes rating 10 against a floor of 5) and the live CSV carries no
  rank-9999 rows. A fund sliding from rank 1 to rank 4 in a stable subgroup is never removed.
  Funds *outside* the band, and whole subgroups whose target falls, still de-allocate normally.
  Accepted for now; `rank = 9999` is the working editorial lever if a fund needs dropping.
