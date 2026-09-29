# SIP follows the customer's stated split

**Status:** design — approved 2026-09-20, audited twice, not yet implemented
**Branch:** `feat-central_investment_preference`
**Scope:** SIP only. Not lumpsum, not rebalancing.
**Supersedes for the SIP path:** the horizon-bucket targeting rule in
`2026-09-05-investment-preferences-s2c-ainv-design.md`
**History:** v1 carried the customer's percentages as data across seven source
files. Audit 1 (cut-biased, 5 lenses) measured that unnecessary and in one case
unsafe, and cut it to three lines. Audit 2 (balanced, 5 lenses) verified the
three lines end to end, found **two ship blockers**, and restored three things
audit 1 had cut. This is the audited version.

---

## 1. Context

> *"When a customer is giving us the preference we should have the SIP in exact
> same percentage that customer is saying. We should not put any logic of our
> own."* — 2026-09-20

Interpretation **(A)**, confirmed: the SIP instalment's **own** split equals the
stated percentages. Holdings are ignored for the SIP. Rebalancing remains the
thing that fixes drift.

Two customer cases, both in scope:

- **Case 1** — the customer states only the asset-class split.
- **Case 2** — the customer states the class split *and* the sub-category split.

### 1.1 The plan already holds the customer's exact percentages

At the plan layer, measured through the real screen validator and engines:

- **Case 2** — 118 screen-validated complete distributions reproduce at
  **0.000pp**. With a non-zero multi-asset sleeve, 189 runs give median 0.000pp,
  p90 0.200pp, max 0.200pp. Every typed `0` honoured as a hard exclusion.
- **Case 1** — class-only asks roll up at **0.000–0.100pp**.

The preference layer is already verbatim at both levels. The SIP only needs to
read the right column.

**Fidelity at the fund layer is amount-dependent** — see §10.1. The plan-layer
numbers above are exact; per-buy ₹100 rounding degrades them on small
instalments.

---

## 2. What is wrong today

`compute_targets` (`AI_Agents/src/additional_investment/ratio.py:38`) weights every
sub-group by **one horizon-bucket column**, chosen by `select_target_bucket`
(`ratio.py:20`) as the first unfunded of short → medium → long.

The weighting is fine. **The selection is the bug.**

- Any preference replaces PAA steps 1-3 with `_no_carveout_buckets`
  (`practical_asset_allocation/pipeline.py:1174`), zeroing the emergency / short /
  medium columns. Deliberate — spec 2026-09-15 §3.
- So for a preference customer `long_term == total` on **every** row.
- Therefore `compute_targets` **at LONG_TERM already weights by the stated
  split.** It just isn't asked to.

### 2.1 Live defect: the ₹0 SIP

**A customer with a preference AND any unfunded goal inside 60 months gets a SIP
of ₹0.** The chosen column is structurally all zeros, `total_weight <= 0`, and
`compute_targets` returns an empty list (`ratio.py:55-57`). Measured: ₹25,000 in,
nothing deployed.

Worse than it first appears: `preference_save_service._refresh_standing_plan`
recomputes and **persists** the SIP on every preference save, so for a customer
with any unfunded sub-60-month goal that write **replaces a previously-working
plan with ₹0** — on a path they never asked for a SIP on.

---

## 3. The rule

> **When the customer has stated a preference, a SIP targets the long-term
> column — which under a preference IS the stated split. No preference → today's
> nearest-unfunded-goal targeting, unchanged.**

---

## 4. Design

One branch, in `ainv_engine/input_builder.py:123-131`, in the existing `else` arm:

```python
short_term_fulfilled, medium_term_fulfilled = await _goal_funding_flags(user, asof)
if cadence is Cadence.SIP_MONTHLY and getattr(allocation_output, "human_override_applied", None) is not None:
    # A stated preference suspends the carve-outs (practical_asset_allocation/
    # pipeline.py:1155-1174): sub-60-month goals leave the plan, so this plan
    # carries no short/medium bucket and its long-term column IS the stated split.
    short_term_fulfilled = medium_term_fulfilled = True
```

**Use the `getattr` form.** A bare attribute read fails two of the 220 baseline
tests — the builder's own stub `_alloc()` returns
`SimpleNamespace(aggregated_subgroups=rows)` with no such attribute
(`tests/test_input_builder.py:77-81`). Measured: literal form → **2 failed, 218
passed** (`AttributeError` in `test_sip_never_attaches_the_map_and_still_runs_flags`
and `test_rebal_buys_passthrough_and_default`); `getattr` form → **220 passed**.
In production it would be worse than a crash: `service.py:284-288` wraps the
builder in `except Exception` and returns `_MSG_ENGINE_ERROR`, turning a SIP into
a silent blocking gate. The parameter is typed `Any` and duck-typed throughout
this builder, so `getattr` matches the file's existing contract.

`apply_human_override` is a strict no-op returning `None` when the preference is
absent or empty (`human_override.py:158`), so `... is not None` is exactly "a
preference shaped this plan". `ainv_engine/service.py:423` already reads the same
flag off the same object.

**No new plumbing, no second preference read, no DB access.** Both call sites are
covered with zero threading: `allocation_output` is `paa_outcome.result` at
`service.py:264` and `sized.result` at `service.py:330`.

**Both conditions are required.** The `else` arm is shared by SIP *and*
lumpsum-without-holdings; the cadence conjunct keeps the no-CAMS lumpsum
untouched. Verified inert off-path: 480 no-preference and 2,880 lumpsum
configurations byte-identical before and after.

### 4.1 Blocker: the no-CAMS rescue stops firing where it was load-bearing

The ₹1-crore sized fallback triggers only `if cadence is SIP_MONTHLY and not
response.buys` (`service.py:314`). Today a tiny-corpus preference customer
produces no buys, so the rescue fires. **After this change a 1-row allocation
produces buys, so the rescue never fires — and ships a degenerate single-class
SIP instead.**

Measured, stated 50/30/20 at ₹25,000/month:

| Corpus | Today | After the 3 lines alone |
|---|---|---|
| 0 | rescue fires, deploys ₹0 (the defect) | rescue fires, deploys ₹25,000 at 50.16/30.00/19.84 ✓ |
| ₹100 | rescue fires, deploys ₹0 | **₹25,000 at 100% equity** ✗ |
| ₹500 | rescue fires, deploys ₹0 | **66.0 / 10.0 / 24.0** ✗ |
| ₹1,000 | rescue fires, deploys ₹0 | 46 / 30 / 24 ✗ |
| ₹10,000+ | — | 50.16 / 30.00 / 19.84 ✓ |

Corpus is floored to ₹100 multiples (`aa_engine/input_builder.py:236`), so the
reachable band is ₹100–900 of declared investable assets — precisely the no-CAMS
cohort the rescue exists for.

**Fix — widen the existing trigger by one clause:**

```python
if cadence is Cadence.SIP_MONTHLY and (
    not response.buys or paa_outcome.result.grand_total < 10_000.0
):
```

₹10,000 is the measured point at which the split becomes correct and stays flat
to ₹10 crore. `grand_total` is on the output (`pipeline.py:196`). Still
app-layer, still zero `AI_Agents/src/` change.

### 4.2 Why not carry the percentages as data (v1)

The engine already reads the customer's split; only the bucket choice was wrong.
And verbatim was **actively worse on a reachable input**:
`resolve_screen_preferences` validates only the per-class *upper* bound
(`screen_preference_service.py:97-113`) and `pins` defaults to `[]`
(`schemas/investment_preferences.py:92`), so `{short_debt: 10, gold: 20}` against
a stated 50/30/20 is **screen-accepted** — and verbatim renormalisation makes it a
33% debt / 67% **gold** SIP with **zero equity**, as live BSE orders. Weighting by
the plan's rows gives 50/30/20.

Recorded so it is not re-litigated: the v1 claim of a 0.2–0.7pp class-rollup error
was measured **without unbundling the multi-asset sleeve** — the real plan-layer
figure is 0.000–0.100pp.

**Refuted and not to be acted on:** an audit lens reported ELSS/direct-stock rows
distorting the split. It measured on the `make_practical_input` fixture
(elss=₹10L, stocks=₹10L). On the real SIP path `paa_engine/input_builder.py:125-126`
forces both to `0.0` and the two rows are **absent** from `aggregated_subgroups`
entirely (6 rows, not 7), so `exclude_subgroups` removes zero rupees.

---

## 5. Narration — required, not optional

Under a preference `target_bucket` comes back `long_term`, and the SIP formatter
body asserts things that are then false. Three regions of `_AINV_FORMATTER_BODY`,
not one:

1. `chat.py:289-296` — tells the model `long_term` means "the short and medium
   goals are funded (or there are none)".
2. `chat.py:379-380` — orders a line on why the split leans the way it does
   "derived from target_bucket".
3. **`chat.py:356` and `:359`** — `plan_by_goals — (SIP) the plan deploys by
   goals`, and "ALWAYS close the category topic with the caveat: … the plan
   spreads by their goals". `plan_by_goals` is a live SIP status
   (`category.py:64, :73`), so these fire on any category ask ("what about
   smallcap?"). Audit 1 checked the identical lines in
   `_AINV_DEFICIT_FORMATTER_BODY` (`chat.py:483`), correctly concluded that twin
   is lumpsum-only, and stopped — on the wrong twin.

Carry **one three-valued key**, `split_source ∈ {your_saved_split,
your_class_split_our_categories, null}`, and branch once across all three regions.

**The honest cost — this is not a free ride on an existing kwarg.** Audit 1 said
to hang it on the `preference` kwarg `build_ainv_facts_pack` already takes
(`chat.py:585`). Verified wrong, three ways:

- On an **ordinary deploy turn** — a customer with a saved preference typing
  "invest 25k a month", the dominant path — `preference` is `None`.
  `_ordinary_deploy` receives it only on preference-*ask* turns
  (`chat.py:933-935`). There is nothing to hang the key on.
- Setting `facts['preference']` fires the block at `chat.py:387-400`, which orders
  the model to narrate what the preference changed from `buy_changes_vs_recommended`
  and to offer the Save-preference button — neither applicable here.
- The case-1 vs case-2 discriminator is **not** on `HumanOverrideApplied`
  (`preference_applied` + `shortfall_reason` only, `human_override.py:95-106`). It
  must come from `subgroup_emphasis`.

So: a **separate kwarg and facts key** (one call site, `chat.py:810`, ~3 lines), a
call-site change in `_ordinary_deploy`, and one read of `subgroup_emphasis` via
`load_human_override_for_user` — the sanctioned mapper at the existing single load
point. This is a **narration** read; it does not shape the run, so the
single-load-point invariant is untouched.

### 5.1 The under-deploy note does not fire where it is needed

`_under_deploy_note` returns `None` when `undeployed_inr <= max(₹100 × n_targets,
0.5% of deploy)` (`chat.py:578`). A preference plan has 5-6 targets, so the
absolute arm is ₹500-600 **regardless of instalment size**. Measured: stated
50/0/50 at a ₹500 SIP leaves ₹200 undeployed — **40% of the instalment** — and the
note is `None`. Make the SIP threshold relative-only (fire above ~2% of the
instalment), or adopt the amount floor in §10.1 so the case cannot arise.

---

## 6. Deliberately unchanged

No-preference customers, both lumpsum paths, rebalancing, the practical allocation
engine, **the whole of `AI_Agents/src/`** — `compute_targets`,
`compute_deficit_targets`, `select_target_bucket`, `select_funds_sip`, every model
and pipeline — the per-fund concentration cap, the `target_bucket` enum, and the
builder debug dict.

The SIP still passes **no ELSS or direct-stock value**: `service.py:203` pins a
`CorpusPin` only on LUMPSUM, and the sized fallback at `:314-326` passes one with
both at `0.0`.

No migration. No schema change. No frontend change.

---

## 7. The forced flags: consumers, and the one record that must stay honest

The branch sets `short_term_fulfilled` / `medium_term_fulfilled` to `True` for a
customer whose near-term goal is genuinely unfunded.

**Traced exhaustively: they have exactly one runtime consumer** —
`pipeline.py:59` → `compute_targets` (`ratio.py:38`) → `select_target_bucket`
(`ratio.py:20`). No cap, ordering, label or reason string reads them, and they do
**not** survive onto `AdditionalInvestmentOutput` (fields are exactly `buys`,
`cadence`, `deploy_amount_inr`, `deployed_inr`, `per_subgroup_target`,
`target_bucket`, `undeployed_inr`). **So no explicit bucket selector is needed and
no engine field is warranted** — the branch cannot tell anything downstream a
falsehood it can act on.

It can tell a **customer** one. `inp.model_dump(mode="json")` emits
`{'short_term_fulfilled': True, 'medium_term_fulfilled': True}`,
`persist_service.py:75` writes that dump into `request_input` JSONB — a column
whose own comment says it exists "for audit" — and the **DPDP export ships it
verbatim** (`user_graph.py:101` selects every FK-reachable table; `export_service.py:64-66`
redacts only credential columns).

So a customer exercising their access right receives a Prozpr record asserting
their unfunded 18-month goal is funded. Nothing reads it back, so it changes no
behaviour — it is purely a record the regulation points at.

**Fix: one line in the existing `request_extras` assembly** (`service.py:385-395`)
recording that the flags were forced and what they truly were. No engine field, no
migration.

### 7.1 The trade-off, stated out loud

A preference customer's SIP stops steering money at their nearest goal. **This
decision was already taken** — spec 2026-09-15 §3 suspends the near-term
carve-outs and step 4 already drops sub-60-month goals from the plan. The SIP
layer is the only place still behaving otherwise.

Second-order: short-term debt exists only as the carve-out row, so a preference
customer's entire debt SIP goes to arbitrage-plus-income. In case 2 the customer
can pin short debt themselves.

---

## 8. Files to touch

| File | Change |
|---|---|
| `ainv_engine/input_builder.py` | The branch (§4) — 3 lines + comment, `getattr` form |
| `ainv_engine/service.py` | Widen the rescue trigger (§4.1); `request_extras` honesty line (§7); `AINV_ENGINE_VERSION` → `ainv-3.3.0` + changelog line |
| `ainv_engine/chat.py` | `split_source` kwarg + facts key, one branch across three prompt regions (§5); `_under_deploy_note` threshold (§5.1) |
| `AI_Agents/src/additional_investment/CLAUDE.md` | Line 25, "Cadence doesn't change the ratio" — gains "except under a stated preference, where SIP targets the long-term column because the plan carries no near-term bucket" |
| `app/domains/additional_investment/CLAUDE.md` | Line 16, "SIP keeps the legacy profile-corpus path" — gains "targeting long-term when a preference is set" |

**Version bump restored.** Audit 1 cut it reasoning "no engine change → no bump".
That misreads the constant: `AINV_ENGINE_VERSION` is an **app-layer** constant
(`service.py:110`) whose changelog records app-layer compute-path changes — 3.0.0
is the rebal-BUY-mirror wiring, exactly this class of change, and nothing under
`AI_Agents/src/` moved for 3.0.0, 3.1.0 or 3.2.0 either. It is stamped on every
persisted run (`persist_service.py:86`) and is the **only forensic marker that
survives a recompute** — and runs are recomputed freely, on every preference save,
from the Invest page and from chat. `created_at` records when a run was computed,
not which code computed it.

**Dead-code review:** nothing goes dead. `compute_targets`,
`compute_deficit_targets`, `select_target_bucket` and both lumpsum paths stay live
and unmodified.

---

## 9. Tests

1. **Case 1** — class-only preference at a stated instalment: the SIP's
   look-through class rollup is within **0.25pp** of the stated mix at ₹25,000.
   Assert against `build_ainv_asset_class_breakdown`
   (`additional_investment_read_service.py:90`), **not** a raw sum of sub-group
   buys — `multi_asset` sits whole in one bucket, so a correctly-honoured 50/30/20
   plan rolls up raw as roughly 25/20/55. Equality is unsatisfiable (§10.1); state
   the tolerance and the amount.
2. **Case 2** — complete sub-category distribution at ₹25,000: each stated share
   within 0.25pp of what was typed. A stated `0` produces no buy.
3. **No preference** — regression guard: the split still follows the bucket column
   and near-term goals still steer it.
4. **The ₹0 defect** — preference + unfunded near-term goal deploys the full
   amount.
5. **Tiny corpus** (§4.1) — preference + corpus ₹100 + ₹25,000 SIP deploys in full
   **and** rolls up to the stated mix. Guards the widened rescue trigger.

Plus the deliberate rewrite of `test_preference_moves_the_ainv_subgroup_split`
(`AI_Agents/tests/test_preference_propagation_e2e.py:61`). It sets both goal flags
to `True`, so it stays **green while its docstring contract goes false**.

Lumpsum needs no new test: the cadence conjunct makes it provably inert (2,880
configs measured identical) and the baseline guards both modes.

Baseline to hold: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing AI_Agents/tests/test_preference_propagation_e2e.py app/domains/additional_investment -q`
→ **220 passed** (confirmed with the `getattr` form; 218 with the literal form).

---

## 10. Accepted degradations

### 10.1 Fidelity is amount-dependent

Per-buy ₹100 rounding is an absolute error, so it scales as ~₹100 ÷ instalment.
`SipCreateRequest.monthly_amount_inr` is `Field(gt=0)` with **no floor**
(`schemas/__init__.py:60`), so small instalments are reachable from the Invest
page. Measured class-rollup error:

| Monthly SIP | Class-rollup error |
|---|---|
| ₹1,00,000+ | 0.000–0.126pp |
| ₹25,000 | max 0.220pp |
| ₹5,000 | 1.216pp |
| ₹3,000 | 5.345pp |
| ₹2,000 | 9.167pp |
| ₹1,000 | 22.857pp (median 11.979) |
| ₹500, stated 50/0/50 | **50.00pp** — realised 0/0/100, only ₹300 of ₹500 deployed |

**Founder decision open (§12):** a ₹5,000 floor on the Invest page caps the class
error at ~1.3pp and makes this whole family of findings disappear. The alternative
is extending rounding-dust reconciliation to the SIP path, which §6 currently
keeps lumpsum-only.

Related and pre-existing: the SIP can debit **more** than the customer typed —
measured ₹1,00,200 against a stated ₹1,00,000, from independent per-buy rounding
with no reconciliation. Preference customers become the headline users of this
path.

### 10.2 Case 1 is class-level only

The sub-category mix inside each class is Prozpr's house view. Must be **said** in
the reply (§5).

### 10.3 Where the plan could not build the typed number

The SIP follows the **plan's** number — the one shown on screen and explained via
`shortfall_reason`. Consistent with D7.

---

## 11. Out of scope — logged, not fixed

- **Per-subgroup absorption ceiling.** A subgroup can take at most
  `n_ranked_funds × max(cap_pct × deploy, sip_fund_cap_floor_inr)`; above a ₹1L
  instalment that is `n_funds × cap_pct`: `medium_beta_equities` **20%**,
  `high_beta_equities` 30%, `low_beta` / `value` / `us_equities` 40%,
  `sector_equities` **0%**, `gold` 100%, `multi_asset` 200%, `short_debt` 180%,
  `arbitrage` 150%, `arbitrage_plus_income` 120%. Anything typed above a row's
  ceiling is silently rerouted to `undeployed_inr`, and the screen validator
  cannot catch it (upper class bound only). Measured, excluding sector: stated
  medium_beta 62 / low_beta 18 / us 10 / value 8 / high_beta 2 at ₹1,00,000 →
  realised 34.48 / 31.03 / 17.24 / 13.79 / 3.45, **27.52pp error, 42% undeployed**.
  Same decision class as D7 — resolve by adding funds. The alternative is a
  per-subgroup max at the screen derived from `n_ranked_funds × cap_pct_for(sg)`;
  **not** in the engine.
- **`sector_equities` has zero ranked funds** yet is a settable screen row. The
  "unreachable in production" comment at `selection.py:126` is false.
- **Zeroing gold inflates equity** (`human_override.py:213-225`).
- **Partial pins via the API.** Harmless under this design (§4.2), but an
  unguarded contract hole worth closing at the screen.
- **`_under_deploy_note`'s emergency-reserve sentence is false for preference
  customers** — there is no emergency carve-out under a preference.
- **`Logics_reference_docs/Additional_Investment.md:22`** states the opposite rule
  as Principle 3 and grounds customer chat answers (`ai_engine/logic_docs.py:36`).
  Reference docs refresh only when a human asks — **flagged for the founder**.

---

## 12. Open for the founder

1. **Minimum monthly SIP** (§10.1). A ₹5,000 floor on the Invest page caps the
   class error at ~1.3pp and removes the small-SIP findings. Product decision.
2. **Invest-page copy.** `SipPlanResponse.target_bucket` flips from `short_term`
   to `long_term` for preference customers, and its schema comment says the
   frontend narrates a friendly label from it. `Prozpr_Frontend` needs checking
   for horizon-derived copy before this ships; the answer belongs in this spec.

---

## 13. Decisions taken

| # | Decision | Date |
|---|---|---|
| D1 | Interpretation (A): the SIP's own split equals the stated percentages; holdings ignored | 2026-09-20 |
| D2 | Scope is SIP only — not lumpsum, not rebalancing | 2026-09-20 |
| D3 | The SIP targets the long-term column under a preference; nothing carries the percentages separately | audit 1 |
| D4 | Where the plan could not build the typed number, the SIP follows the plan | audit 1 |
| D5 | Whole-percentage-point precision accepted at the plan layer; fund-layer fidelity is amount-dependent (§10.1) | 2026-09-20 / audit 2 |
| D6 | The ₹0 SIP is fixed by this change, not separately | 2026-09-20 |
| D7 | Unbuildable asks — unranked categories, absorption ceilings — resolved by adding funds, not by engine logic | 2026-09-20 |
| D8 | No change anywhere under `AI_Agents/src/`. The app-layer `AINV_ENGINE_VERSION` bump is restored | audit 1, amended audit 2 |
| D9 | Forced flags are safe (one consumer, traced) but must be labelled in `request_extras` for DPDP record honesty | audit 2 |
