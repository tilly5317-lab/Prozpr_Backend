# Debt-switch netting — design note

**Date:** 2026-07-18
**Status:** **IMPLEMENTED** 2026-07-18, uncommitted in the working tree. `ENGINE_VERSION` 1.1.0 → 1.2.0.
Files: new `steps/step2b_suppress_debt_switch.py` + `Testing/test_step2b_debt_netting.py` (8 tests);
edits to `models.py` (one defaulted field), `pipeline.py`, `step6_presentation.py`, `config.py`.
Suite 62 → 76 engine tests passing (14 for this step), 280 app-layer, no regressions. Measured on
both harnesses: selling −68%, tax −96%, lifecycle trades 237 → 199. Netting confirmed debt-only
(non-debt knock-on ₹84,404, the §10 watch item, real but 1.2%). Force-exits and NEUTRAL migrations
preserved; `Σbuys ≤ Σsells` holds. The §3 partial-net hazard is handled — a residual below the
materiality bar is absorbed deliberately rather than left to die in step4, so the recorded
adjustment matches the real change.

**A feature flag WAS added** (this note previously said otherwise): `DEBT_SWITCH_NETTING_ENABLED`
in `config.py`, env `REBAL_DEBT_SWITCH_NETTING=0`, stamped into `KnobSnapshot` alongside
`debt_netting_subgroups` and covered by a test. `DEBT_NETTING_POOL` lives in `tables.py`.

**Still a real deviation:** the "pool key + pair policy" abstraction (§9) was NOT built — the step
is debt-only, so adding equity later means reshaping it rather than adding a rule.

> **CORRECTION (2026-07-19).** §4's claim that "cash conservation is structural… `net_cash_flow_inr`
> is unchanged by construction" is **wrong**. `Σbuys ≤ Σsells` genuinely holds — confirmed on all 5
> profiles — but it is enforced by step4 re-deriving `scale` from *realised* sells and flooring each
> buy (`step4:309-322`), **not** by this step's leg symmetry, which the residual absorption breaks
> on purpose. `net_cash_flow_inr` does move. Do not write the equity policy against the wrong
> guarantee.
**Scope:** rebalancing engine, **plus a required app-layer + schema change** — see below.

> **CORRECTION (2026-07-19, found in review).** This note originally said "no app-layer change, no
> schema change," and that sentence licensed a shipped blocker. Step2b emits a new
> `WarningCode.DEBT_SWITCH_SUPPRESSED`; `rebalancing_persist_service.py:239` converts it via
> `RebalancingWarningCode(warning.code.value)`, and the app-side enum
> (`app/domains/rebalancing/models/rebalancing_warning.py`) did not have that member — so **every
> run where netting fired raised `ValueError` on persist and 500'd the turn, after the engine had
> already computed a plan.** Netting fires on 4 of 5 sim profiles, so this was the normal path.
>
> Required, and **must be applied before the 1.2.0 code deploys**:
> 1. `DEBT_SWITCH_SUPPRESSED` added to `RebalancingWarningCode`.
> 2. `migrations/sql/add_debt_switch_suppressed_warning_code.sql` — the column binds a real
>    Postgres type (`create_constraint=True`), so the Python edit alone is insufficient. Targeted
>    DDL because alembic cannot run in this environment.
> 3. `app/domains/rebalancing/tests/test_warning_code_parity.py` fails if the two enums ever drift
>    again.

This is a design *note*, not a spec. The change is ~150 lines. What follows is mostly the
reasoning, because the obvious designs are wrong in non-obvious ways and were each killed by
evidence. Read the "Rejected designs" and "Corrections" sections before proposing an alternative.

---

## 1. The problem

The rebalancing engine sells one debt fund to buy another debt fund. Total debt exposure is
unchanged, the funds are economically equivalent, and the customer pays real tax for it.

Measured on the Neha Reddy persona (2-year lifecycle sim, `AI_Agents/lifecycle_sim_testing/`):

| | |
|---|---|
| MF sell value, 5 rebalances | ₹34,94,087 |
| Same-subgroup churn (strict) | ₹6,46,615 (18.5%) |
| Churn with arbitrage sleeves merged | **₹18,61,965 (53.3%)** |

The ~₹12.15L gap between those two numbers *is* the debt problem: swaps that cross the
`arbitrage` / `arbitrage_plus_income` boundary, which strict subgroup accounting does not see
as churn at all.

Worst single case, m18: sell ₹7,97,627 HDFC Income Plus Arbitrage FOF ("Trim back to target"),
buy ₹8,03,800 Kotak Arbitrage ("Top up to target"). Rank-1 to rank-1, ₹14,332 of tax, zero
change in exposure.

**Root cause is upstream.** The allocation engine picks a debt wrapper from the customer's tax
rate and the goal's remaining tenure (`asset_allocation_pydantic/tables.py:172-180`). As tax
rate or tenure drifts, the chosen wrapper changes, one subgroup's target falls and another's
rises, and rebalancing dutifully executes the switch.

---

## 2. The rule

> A held, recommended debt fund is never sold in order to buy another debt fund.

The wrapper choice is worth making **once, at purchase, and is never revisited with money
already deployed.** Switching wrappers buys nothing on returns (product assumption: all debt
funds deliver similar returns) while costing realised tax and a reset holding period.

Note the treatments are *not* identical — see §7 — so the argument is not "these are the same
thing." It is: **you pay certain tax today for a marginally better treatment on future gains,
when the returns are the same either way.** Don't pay now for a future option.

---

## 3. The design

A new pure step, `steps/step2b_suppress_debt_switch.py`, inserted between step 2 and step 3
(`pipeline.py:101`). Signature mirrors the other steps: `apply(rows, request) -> rows`.

It cancels matched debt sell/buy **intents** before any lot selection or tax arithmetic happens.

**Matching.** One pool across `{short_debt, arbitrage, arbitrage_plus_income}`, not per-pair.

```
cancel_total = min( Σ eligible debt sells,
                    Σ eligible debt buys − reserved force-exit proceeds )
```

allocated pro-rata across both sides in raw `Decimal`. No rounding rule is needed — netting
happens in intent space, upstream of `floor_to_step` (`step4:320`).

**Eligibility** (apply before pairing, not as a post-filter):

- Sell legs exclude `exit_flag` rows — a bad fund is still a bad fund.
- Sell legs exclude `rank == 0` (NEUTRAL) rows. `pipeline.py:67-73` has *already* deducted
  their ST value from the subgroup's rank-1 target; netting them double-counts. Off-list debt
  must still migrate.
- Buy capacity is reduced by `Σ present_allocation_inr` of debt `exit_flag` rows. Those
  proceeds have a destination and must keep it, or `step6:294` surfaces them as negative
  `net_cash_flow_inr`.
- Genuine de-allocation needs **no code**: if total debt is shrinking there is no matching buy
  to cancel against, so the sells stand.

**Fields written** (rebuild rows via `**r.model_dump()`, the house idiom at `step2:53` and
`step3:44` — not `model_copy(update=...)`, which skips validators):

| Field | Action |
|---|---|
| `diff` | reduce by the row's share of `cancel_total`; clamp at 0, never cross sign |
| `final_target_amount` | move in lockstep so `diff == final_target_amount − present_allocation_inr` still holds |
| `worth_to_change` | **recompute** — re-run `step2:48-50` with the new target. Mandatory: the threshold scale is `max(final_target, present)` and it moves too |
| `final_target_pct` | recompute via `_pct_of_corpus` (`step1:32`; derived pre-netting at `step1:106`, and persisted) |
| `netted_target_adjustment_inr` | **new**, signed, default 0. `step6:217` adds it into `goal_target` |
| `target_amount_pre_cap` | **leave alone** — see §4 |

**Model change.** `FundRowInput → AfterStep1 → ... → AfterStep5` is a clean inheritance chain
(`models.py:30, 86, 94, 100, 105, 121`), so one optional field on `FundRowAfterStep2` propagates
automatically. Because it has a default, none of the ~48 fixture construction sites break.

**No schema change.** `final_target_amount` and `final_target_pct` are already persisted columns
(`rebalancing_fund_row.py:78, 81`), and `goal_target_inr` on the subgroup summary picks up the
adjustment through step 6. `netted_target_adjustment_inr` need not be persisted. This matters —
alembic is currently unusable on the dev DB (`alembic_version` points at a revision absent from
`alembic/versions/`).

Bump `ENGINE_VERSION` (`config.py`). Nothing keys off it; it is documentation.

---

## 4. Why this placement is safe

Verified by direct reading, not inference:

- **Steps 3-5 read no target fields.** `grep` for `target_amount_pre_cap|final_target_amount|
  final_target_pct|target_pre_cap_pct` across `step4` and `step5` returns nothing; `step3:34-37`
  reads only `exit_flag`, `worth_to_change`, `diff` and the ST/LT value/cost fields. So netting
  at 2b is a legitimate *input transformation* that the whole downstream is blind to.
- **Step 4 cannot recreate a cancelled pair.** All three candidate pools use strict inequality
  (`step4:275-277`) and `target_buy = sum(r.diff for r in buyers)` (`step4:279`). A row netted to
  `diff == 0` lands in no pool, and buy demand is a pure function of diffs fixed before any sort.
- **Cash conservation is structural.** Equal amounts come off both legs, so
  `net_cash_flow_inr = total_buy − total_sell` is unchanged by construction.

**Why targets move via a new additive field rather than by overwriting `target_amount_pre_cap`:**
`pipeline.py:61-73` sets that field so its per-subgroup sum equals the practical allocation
engine's output, and `models.py:290` ships `practical_allocation` verbatim in the same response.
Overwrite it and the payload carries two different numbers for "how much arbitrage should you
hold," and the persisted audit trail is corrupted.

---

## 5. Rejected designs

**Fix the subgroup targets (attempted first, killed by audit).** Make a holding in any debt
subgroup satisfy a target in any other, via a `satisfies(held_sg, target_sg) -> bool` helper at
three call sites. Eight blockers, of which three are fatal:

- A boolean predicate **cannot conserve rupees.** The problem needs *amount attribution* —
  deciding which single target a held rupee is spent against. Widening the lookup subtracts the
  same balance from all three debt deficits. Ideals ₹5L/₹3L/₹2L against ₹6L held: true gap ₹4L,
  computed ₹0, and `ratio.py:117` then renormalises the vanished debt weight onto equity, so the
  lumpsum silently over-buys equity.
- **It does not deliver the rule.** `input_builder.py:272` gives a target only to rank-1 rows;
  ranks 2+ get zero. A held rank-3 arbitrage fund therefore gets `diff = −present` and is sold to
  fund the rank-1 buy — literally the forbidden trade. All three call sites touch only rank 0 and
  rank 1.
- **The helper cannot live where it needs to.** `scheme_classification.py` is under
  `app/domains/`; two call sites are under `AI_Agents/src/`, which imports zero `app.*` modules.

**Net after step 5 (proposed, then rejected).** Cancelling completed trades rather than intents.
`scale = total_sold_final / target_buy` (`step4:313`) and the per-row `floor_to_step`
(`step4:320`) are **not invertible**. Cancel a sell afterwards and every unrelated buyer keeps an
amount computed against a stale `target_buy`, funded by cash that no longer exists — a
hand-simulation put an equity buyer at ₹3,12,500 instead of ₹5,00,000 — and it retroactively
breaks the live `Σbuys ≤ Σsells` assertion. There is also no unique definition of "the matched
portion" once either leg has been throttled. The only correct after-step-5 implementation is
"re-run step 4's buy distribution," which is not a netting step.

---

## 6. Decisions on record

| Decision | Rationale |
|---|---|
| **Scope: all debt-for-debt**, cross-subgroup *and* same-subgroup | Rank differences among debt funds don't matter to the product. Consequence accepted: a mediocre debt fund is only ever upgraded by new money, never by rebalancing |
| **Cap-spill pairs get netted** | The 30% per-fund cap governs where *new money* lands, not what we force a customer to sell. A debt fund may sit above 30% until inflows bring it down |
| **Bracket-crossing is not exempted** | A customer whose tax rate rises past the 15%/20% routing threshold never migrates existing debt into the better wrapper — only new money goes there. Deliberate: you'd pay slab tax today to buy a benefit worth roughly nothing when returns are equal |
| **No concentration ceiling** (decided 2026-07-18, after measurement) | A ceiling above the cap — trim only past e.g. 1.5× — was considered and **declined**. The cap governs deployment only; concentration is corrected by inflows, never by a forced sale. See the measured consequence below before revisiting |

### Measured consequence of declining a ceiling

Before this change every profile in the 5-profile sweep landed **exactly at its 30% cap**. After:

| Profile | Top debt fund, % of total corpus | vs cap |
|---|---|---|
| Aarav Gupta | 72.2% | +42.2pp |
| **Lakshmi Iyer** | **87.5%** | **+57.5pp** |
| Mohammed Faisal | 30.0% | — |
| Neha Reddy | 40.4% | +10.4pp |
| Harpreet Singh | 47.8% | +17.8pp |

Lakshmi is the case to weigh if this is ever reopened: 58, conservative, and on a ₹7,000/month SIP
against a ₹95L corpus, inflows will not correct 87.5% single-fund concentration in any meaningful
timeframe. Mitigating context: `synth_holdings` seeds 100% of each subgroup into its rank-1 fund,
so these are worst cases by construction — real CAS-ingested portfolios should start less
concentrated. **Re-measure on real portfolios before concluding the exposure is acceptable.**
| **Ships before the tax fix** | Measured: the tax fix changes churn by ₹0 (§8). Shipping it first would produce a flat number on a ticket that looks like it did nothing |

---

## 7. Tax treatments (confirmed by product owner, 2026-07-18)

| Treatment | ST/LT boundary | STCG | LTCG | ₹1,25,000 exemption |
|---|---|---|---|---|
| Equity + arbitrage | 12 months | 20% | 12.5% | yes |
| `arbitrage_plus_income` | 24 months | 20% | 12.5% | **no** |
| Debt | **no ST/LT split** | slab | — | no |
| Gold / silver / intl / FoF | *pending advisor* | | | |

No acquisition-date cutoff for debt, so no lot-cohort splitting is required.

These belong to the **separate** tax workstream (defects 3+4+5), not to this change. Recorded
here because they establish that `arbitrage` and `arbitrage_plus_income` are *not* tax-identical,
which is why §2 states the rationale as "don't pay now for a future option" rather than "these
are the same thing."

---

## 8. Corrections to earlier reasoning

Documented so they are not repeated. Each of these was asserted during design and later
disproved:

1. **"Netting after step 5 wastes STCG budget."** False — the saving is exactly ₹0. Every
   nettable debt sell is an *optional* sell, and optional sells are hard-wired LT-only
   (`st_available = None if is_forced else Decimal(0)`, `step4:201`). The ST branch never runs,
   `_apply_stcg_budget` is never called.
2. **"Netting after step 5 shortchanges other buyers."** Half true, and backwards. When
   `total_sold_final >= target_buy`, `scale == 1` and the placements are byte-identical.
   Divergence exists only in the shortfall regime, where after-step-5 *over*-funds unrelated
   buyers. **No test with `scale < 1` exists — build that fixture.**
3. **"Suppressing debt sells means fewer bad funds get fully exited."** False, same root cause as
   (1). `step5:41-43` builds the loss pool from `pass1_realised_stcg`, which is always 0 for
   optional sells. Netting removes no ST losses, so `extra_headroom` is unchanged.
4. **"Reversal needs a lot ledger."** False — `_sell_from_row` is strictly proportional
   (`step4:105`) and `step5:86-87` already reconstructs a pass-1 sell. Reversal is trivial; it is
   `scale` and `floor_to_step` that are not invertible.
5. **"Fixing the tax pricing will reduce churn."** False, and **measured**: 5 personas × 24
   months, total sells identical to the rupee, buys byte-identical at ₹19,852,600. In the one
   persona that moved at all, ₹13,282 of selling relocated from debt to equity with the total
   unchanged. Trade count went *up*, 237 → 239. `_tax_cheapness_key` decides only *which* rows
   are drained, never how many rupees.

---

## 9. What this does NOT fix

- **Equity cap-spill churn (#1).** Designed in
  [`2026-07-19-holdings-aware-targets-design.md`](2026-07-19-holdings-aware-targets-design.md)
  (the 2026-07-18 rank-downgrade note is superseded). Not yet built.

  > **This step is NOT made redundant by that work** — a scoping pass claimed it would be, on a
  > measurement taken from the single-rebalance sweep, which structurally cannot show wrapper
  > switching (there is no tenure or tax-rate drift at month 0). Measured on the 24-month lifecycle
  > instead: **₹57,58,423 of cross-subgroup debt wrapper switching survives** the holdings-aware
  > changes, and it *oscillates* — Neha alone churns ₹21.1L across four rebalances moving back and
  > forth between two wrappers. Those changes raise a ceiling and compare ranks *within* a subgroup;
  > only this step addresses a subgroup-level target move. **Use the lifecycle sim, not the sweep,
  > for anything debt-related.**

  > **One thing from that note is time-sensitive and belongs in *this* implementation.** The
  > shared abstraction must be **"pool key + pair policy"**, not the eligibility-predicate
  > parameter originally sketched here. Debt pools *across three subgroups* and ignores rank;
  > equity pools *within one subgroup* and is rank-directional — a predicate parameter cannot
  > express a difference in pool key. Build that shape now; retrofitting it later is the expensive
  > order. Two quantities must also be global rather than per-policy once both exist: the
  > force-exit reserve (an equity cancellation in one subgroup can strand a debt reserve in
  > another) and the buy-demand impact on `target_buy` (`step4:279`), which the watch item in §10
  > measured for debt alone.
- **Small trades (#2).** Netting removes whole pairs; it adds no minimum trade size. Note the 10%
  gate at `step2:45-50` is not broken — it gates *intent*, while the emitted trade is sized later
  in step 4. A fix needs a second gate on the final trade.
- **Sell-then-SIP-buy (#3).** Different engine. `engines.py:144-149` filters the SIP mirror to
  BUY trades so sold ISINs are invisible, and `selection.py:105-108` walks the subgroup ranking
  straight back onto the just-sold fund. Neither engine keeps a do-not-repurchase list.
- **The tax model (3+4+5).** Separate, sequenced after this.

---

## 10. Verification plan

Unit tests (the step is pure, so these are cheap): full cancel · partial cancel · force-exit
carve-out · rank-0 carve-out · shrinking-debt de-allocation · `worth_to_change` recomputed on a
partial net · conservation (`diff == final_target − present`) · subgroup `goal_target` reconciles
with `suggested_final`.

Integration: `Σ subgroup targets == total_corpus` still holds. **No such assertion exists today** —
add it.

Measurement: before/after lifecycle sim across all 5 personas. Report debt sell value, equity
sell value, total sell value, trade count, total tax. This is the deliverable that de-risks the
change — the engine has **no golden fixtures** (`test_e2e_workbook.py` is skipped,
`workbook_baseline.json` does not exist) and **no non-equity fixture anywhere in the suite**.

Watch item, not a blocker: cancelling debt buys shrinks pooled `remaining_buy_demand`, so step 4's
optional-sell loop may break earlier (`step4:206-207`) and some equity trims stop happening. Sells
are pooled across asset classes, not matched to buys by class. Expected to be near-zero today
(debt is mispriced as cheap and sorts to the front of the queue, so nettable debt sells would have
executed anyway); expected to appear once the tax fix pushes debt to the back. Measure it then.

---

## 11. Open items

- Gold / silver / international / other-FoF tax treatment — with the product owner's advisor.
- Whether `stcg_offset_budget_inr` is ever non-zero in production. `input_builder.py:449-453`
  passes `None` on every run while `models.py:150-157` states production callers never should.
  Cheap query, unrelated to this change but adjacent to it.
