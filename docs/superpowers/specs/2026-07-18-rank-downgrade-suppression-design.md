# Rank-downgrade suppression (#1) — design note

**Date:** 2026-07-18
**Status:** designed, **build deferred** — see §1 before scheduling
**Depends on:** [`2026-07-18-debt-switch-netting-design.md`](2026-07-18-debt-switch-netting-design.md) (#4).
Read that first; this extends the same step.
**Scope:** rebalancing engine only. No app-layer change, no schema change.

---

## 1. Why this is deferred — read first

Measured across all 5 personas × 5 rebalances of the lifecycle sim:

| | Executed value |
|---|---|
| **#1** — non-debt rank-downgrade pairs | **₹7,44,707** |
| #4 — debt pool | ₹57,08,858 |
| Total MF sell value | ₹1,98,60,249 |

#1 is ~3.7% of sell value against #4's ~29%. It touches **9 of 237 trades**, and the attributable
tax is **≈₹13,960** (realised STCG is ₹0 everywhere — optional sells are LT-only at `step4:201`).

Two facts that matter more than the total:

- **All four events are in `multi_asset`.** No true `*_equities` subgroup, and no
  `gold_commodities`, produced a rank≥2 buy in 25 runs. The equity churn this rule was written
  for does not currently occur. (The originating customer example — sell ICICI Pru Equity & Debt,
  buy Parag Parikh Flexi Cap, m0 — is itself a `multi_asset` trade.)
- **89.6% is a month-0 fixture artifact.** Three of four events occur on the first rebalance
  against `synth_holdings`, which seeds 100% of each subgroup into its rank-1 fund and therefore
  *guarantees* a cap breach. Steady state across m6-m24 is **one event, ₹85,891.**

**Evidence quality is weak and should be treated as such.** Four events, one subgroup, five
synthetic personas — and the adversarial refuter for this measurement died on an API error, so the
numbers are unverified single-source (they did reproduce an independent earlier figure to within
0.1%).

**Recommended next step before building:** measure per-subgroup rank concentration in real
CAS-ingested portfolios. If customers typically hold 2-3 funds per subgroup, this problem is
largely synthetic and #4 plus the shared abstraction (§4) suffices. If they are concentrated the
way the fixture is, re-measure and build.

---

## 2. The rule

> Within a subgroup, a sell intent on a fund of rank *i* may not fund a buy intent on a fund of
> rank *j* where `1 ≤ i < j`. Such matched intent is cancelled on both legs before tax
> classification.

Upgrades (selling a worse-ranked fund to buy a better-ranked one) proceed untouched. No magnitude
band is needed — the `rank_delta > 2` idea was only ever required to suppress *noise upgrades*,
and upgrades are not what drives the churn.

**Exclusions**, each measured or code-verified:

1. **`rank == 0` (NEUTRAL) sells are never cancellable.** Rank 0 means "held but off-list"; those
   rows migrate their LT portion into the recommended fund. A naive `sell.rank < buy.rank`
   evaluates `0 < 1` and suppresses exactly that. **Measured cost of getting this wrong:
   ₹1,60,859 — 19% of the entire prize, spent backwards.** In the sim it is masked because those
   rows also carry `exit_flag`; **in production `exit_flag` is dead** (`input_builder.py:51`
   hardcodes `_DEFAULT_FUND_RATING = 10` against `EXIT_FLOOR_RATING = 5`, and the live ranking CSV
   has zero rank-9999 rows), so this carve-out is the only protection that actually runs.
   Hence the predicate is `1 <= sell.rank < buy.rank`, and rank-0 sorts as *worst*, not best.
2. **`exit_flag` sells are never cancellable.** Gate on `exit_flag` (`step2:46`), **not** on
   `rank == FORCE_EXIT_RANK` — the rating branch can place a forced sell at rank 1-2.
3. **Eligibility mirrors step 4's own pools exactly** — sells `worth_to_change and diff < 0 and
   not exit_flag`, buys `worth_to_change and diff > 0 and is_recommended` (`step4:275-277`).
   Anything looser nets intent step 4 would never have executed.
4. **Genuine subgroup de-allocation needs no code** — no matching buy exists to cancel against.

---

## 3. The matching algorithm

Equity cannot pool the way debt does. Whether a pair cancels depends on *which* sell funds *which*
buy: a subgroup with a rank-1 sell (cap-spilled), a rank-3 sell and a rank-2 buy must cancel the
rank-1→rank-2 portion and preserve the rank-3→rank-2 portion.

```python
INF = 10**9
def sort_rank(r):                      # rank 0 (off-list) sorts WORST; 9999 already sorts last
    return INF if r.rank == 0 else r.rank

def rank_matched(group):               # one asset_subgroup
    sells = [r for r in group if r.worth_to_change and r.diff < 0 and not r.exit_flag]
    buys  = [r for r in group if r.worth_to_change and r.diff > 0 and r.is_recommended]
    S = sum(-r.diff for r in sells); B = sum(r.diff for r in buys)

    # phase 1 — maximum LEGITIMATE (upgrade) flow, descending-rank sweep.
    # Serving a buy before the same-rank sell enters the pool enforces strict rank_sell > rank_buy.
    pool = U = Decimal(0)
    for r in sorted(sells + buys, key=lambda r: (-sort_rank(r), r.isin)):
        if r.diff > 0: take = min(r.diff, pool); pool -= take; U += take
        else:          pool += -r.diff

    # phase 2 — cancel the non-upgrade remainder.
    C = max(min(S, B) - U, 0)
    take_from(sells, C, order=ascending  sort_rank)   # best-ranked sells spared first
    take_from(buys,  C, order=descending sort_rank)   # worst-ranked buys shrunk first
```

**Correctness.** A buy at rank *j* is fundable by exactly the sells at rank > *j*, so the
neighbourhoods are nested and the bipartite graph is a staircase — worst-rank-first greedy attains
max-flow, and `U` is invariant to consumption order.

**Conservation.** Equal rupees come off both sides, so `diff == final_target_amount −
present_allocation_inr` holds per row and `net_cash_flow_inr` (`step6:294`) is unchanged. The
budgets dominate: `S − U ≥ C` and `B − U ≥ C`, so the same scalar `C` is always placeable on each
side.

**Determinism** comes from three fixed sort orders, with `isin` as tie-break — rank uniqueness is
a property of the CSV, not an enforced invariant (see §6 Q8).

### The trap: do not reuse #4's "reserve" pattern

Aarav m24 is the contested case — sells at rank 1 (₹85,891) and rank 4 (₹27,777) against buys at
rank 2 (₹86,391) and rank 3 (₹1,53,518). Correct answer: `U = 27,777` (the rank-4 sell is a
legitimate upgrade), `C = 85,891` (the rank-1 sell cancels in full). **If you reserve buy capacity
for protected sells the way #4 does, you reserve for the rank-4 upgrade and cancel nothing** — the
exact inversion of intent. Compute `U` first, then cancel; do not reserve.

### Field writes

Identical to #4 — `diff`, `final_target_amount`, `worth_to_change` recomputed via `step2:48-50`,
`final_target_pct` — with **one divergence**: `netted_target_adjustment_inr` is **not** written for
the equity rule. `step6:217` sums `target_amount_pre_cap` per subgroup, and rank-matched pairs are
intra-subgroup by construction, so both legs' target moves cancel within one subgroup and
`goal_target` is already correct. Writing the field would make the response under-report.

> This does **not** remove the field from #4. Debt netting is *cross*-subgroup — it moves target
> from `arbitrage_plus_income` to `arbitrage` — which is precisely the case the field exists for.
> Keep it there, skip it here.

**Cap detection.** Do not infer a cap-bound row as `final_target_amount < target_amount_pre_cap`.
`step1:98` rounds to the nearest ₹100 (`round_to_step`), so that predicate fires spuriously on
rounding alone, up to half a step. Reconstructing the cap from `max_pct` is also unsafe — it is a
`float` (`step1:73`). **Have step 1 emit an explicit `cap_bound: bool`**, set where the engine
already knows the answer at `step1:78` (`if with_spill > cap_amount`).

**Partial-net hazard.** Recomputing `worth_to_change` on a partially-netted row can flip it to
`False` while a non-zero residual remains, deleting that intent from step 4's pools and spreading
the shortfall onto unrelated buyers via `scale` (`step4:313`). Handle in the shared field-writing
helper by extending any cancellation to the row's full residual; the iteration terminates because
each round strictly reduces total intent.

---

## 4. How it composes with #4 — settle this before #4 is built

**One step, one pass, per-subgroup policy dispatch:**

```python
def netting_key(row):
    if row.asset_subgroup in DEBT_POOL: return ("pooled",       "DEBT")            # #4
    return                              ("rank_matched", row.asset_subgroup)       # #1
```

`netting_key` is a total function on `asset_subgroup`, so the partition is disjoint and exhaustive
by construction — no row can be double-netted or missed. Two passes would need that proved
explicitly; one pass gets it free.

**The shared abstraction must be "pool key + pair policy", not the "eligibility predicate"
originally proposed in the #4 note.** Debt pools *across three subgroups* and ignores rank; equity
pools *within one subgroup* and is rank-directional. A predicate parameter cannot express a
difference in pool key. **This is the one part of #1 that is time-sensitive: build the shape into
#4 now, because retrofitting it afterwards is the expensive order.**

Two quantities must be computed **globally, once, after both policies propose cancellations and
before any are applied:**

1. **Force-exit reserve.** Forced sells execute portfolio-wide and unconditionally
   (`step4:294-301`), before any buy-demand gate. #4's debt-scoped reserve can be stranded by
   equity buy-cancellation in a *different* subgroup, surfacing as negative `net_cash_flow_inr`.
   The reserve must span both policies.
2. **Global buy-demand impact.** Cancelling buys shrinks portfolio-wide `target_buy`
   (`step4:279`), which gates the optional-sell loop (`step4:206`). #4 files this as a near-zero
   watch item — but that was measured for debt alone. Re-measure with both policies on.

---

## 5. Cases that would be wrongly suppressed

| Case | Suppressed by | Fix | Measured cost |
|---|---|---|---|
| NEUTRAL off-list migration (rank-0 sell → rank-1 buy) | naive `sell.rank < buy.rank` | predicate `1 <= sell.rank < buy.rank`; rank-0 sorts worst | **₹1,60,859** |
| Legitimate upgrade contested by a downgrade in the same subgroup | any reserve-style matcher | compute `U` first, cancel the remainder | ₹27,777 |
| Force-exit of a bad fund | omitting the `exit_flag` carve-out | gate on `exit_flag` | ₹1,32,486 in sim; ₹0 in production (dead code) |
| Genuine subgroup de-allocation | nothing | none needed | ₹0 |

Two things the rule **cannot see** — scope questions, not false positives:

- **Cap trims with no counterparty buy.** Overflow past the last rank is booked to
  `unrebalanced_remainder_inr` (`step1:84`) with no paired buy, so no netting step reaches it.
  That field is computed in step 1, upstream of 2b, and never updated — so after suppression the
  response reports overflow as unrebalanced while the fund is deliberately parked above its cap.
  Decide whether to recompute or document (§6 Q6).
- **F3-B consolidation runs after step 2b**, on the final response. Its `allowed_categories` path
  filters by `sub_category` before sorting by rank while preserving every sell, so it can
  re-create a downgrade pair in the chat-emitted plan. **The no-downgrade property is not an
  invariant of what the customer actually sees** (§6 Q7).

---

## 6. Open questions

1. **Ship or defer?** Recommendation: defer, and re-measure on real CAS portfolios first (§1).
2. **General rank-matching, or scope to `multi_asset` only?** No `*_equities` subgroup produced a
   cap-spill buy in 25 runs; all measured exposure is `multi_asset`.
3. **Does `gold_commodities` get the rank-matched policy or the debt-style pooled policy?**
   Depth-10 ladder at a 10% cap makes it the most spill-prone subgroup in the file. If gold funds
   are fungible the way debt funds are, pooling cancels more.
4. **Is a rank-0 NEUTRAL sell funding a rank-2 spill buy a forbidden downgrade or a permitted
   migration?** §2 assumes permitted.
5. **Do we accept a fund sitting above its cap in equity, as already accepted for debt?** Equity's
   per-fund cap is a concentration-risk control rather than a tax-wrapper artifact, so the
   argument is not identical. Concrete figure: under this rule Harpreet's rank-1 `multi_asset`
   fund stays **₹3,63,873 above its 20% cap**.
6. **Should a cap-driven trim with no paired buy also be suppressed?** The principle says yes; no
   netting step can see it, so it needs separate machinery.
7. **Does the no-downgrade invariant apply to the F3-B consolidated chat plan, or only the
   persisted engine plan?**
8. **Should rank uniqueness be validated at CSV load** (`fund_rank.py:75-112`) rather than relying
   on an `isin` tie-break in two places? A duplicate rank-1 today already means two rows both
   receive the full subgroup target (`pipeline.py:77-84`) — a pre-existing bug this change
   inherits. If ties are tolerated, the same tie-break must be added to `step1:53-56`, which sorts
   the cap cascade by rank alone and relies on stable sort; otherwise step 1 spills onto one fund
   while step 2b protects another.

---

## 7. Verified directly (not agent-reported)

For anyone auditing this note — the following were read in the tree rather than taken on report:

- `step1:53-56` — the cap walk covers `1 <= rank < FORCE_EXIT_RANK`; rank-0 and force-exit are
  handled in separate loops (`:114-140`).
- `step1:82` — `spill_in[i + 1] += overflow`. Overflow **always** cascades to the next *worse*
  rank, never backwards. Cap-spill can therefore only ever produce a better-ranked sell paired
  with a worse-ranked buy, which is exactly the shape this rule targets.
- `step1:98` — `round_to_step` breaks the naive cap-detection predicate (see §3).
- `step2:44-50` — `diff`, `exit_flag` and `worth_to_change` construction, and the three lines to
  re-run when recomputing.
- `step3:34`, `step4:275-279` — candidate pools use strict inequality; `target_buy` is a pure
  function of buyer diffs fixed before any sort.
- `models.py:30, 86, 94, 100, 105, 121` — clean inheritance chain, so a defaulted field added at
  one level propagates.

**Known stale comment, pre-existing, not ours to fix here:** `step1:111-113` states NEUTRAL rows
preserve `target_amount_pre_cap` "(= present holding … so step2 produces `diff = 0`)". That is
wrong — `input_builder.py:379` sets it to `split.st_value_inr`, the short-term portion only, so
`diff = −lt_value` and NEUTRAL rows *are* sell candidates. This is why the rank-0 carve-out in §2
is load-bearing rather than defensive.
