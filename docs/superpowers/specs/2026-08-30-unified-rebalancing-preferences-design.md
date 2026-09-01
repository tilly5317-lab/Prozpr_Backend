# Rebalancing Preferences — market-cap tilt + composed engine-side preferences

**Date:** 2026-08-30
**Status:** Design (audited; scoped to Phase 1)
**Builds on:** `2026-08-24-investment-preferences-design.md` (generalises its `asset_class_tilt` spine)

> This spec was audited against the engine code on 2026-08-30 (three read-only
> tracks). The audit **confirmed** the market-cap tilt, the detector/router
> composition, the SEBI-tables change, and the integration hygiene; it **corrected**
> several "reuse existing machinery" assumptions for the constraints. Scope was then
> cut to a shippable Phase 1. The load-bearing audit facts are in §12.

---

## 1. Why

Rebalancing chat handles exactly one preference cleanly — an asset-class exposure
change ("make it 100% equity") — via the right spine: set a target, **re-run the
engine**, show recommended vs. requested. Two failures motivated this work:

1. **Combined asks silently drop fields.** `"only equity, more mid cap, max 4
   funds"` should honour all three; the handler routes on the first matching
   signal and returns ([chat.py:768-829](../../../app/domains/rebalancing/services/rebal_engine/chat.py)),
   so an `only equity` scope pre-empts the rest.
2. **Sub-category "tilts" can't move the portfolio.** `"more small cap"` falls into
   the consolidation buy-side shuffle, which can never *sell* large-cap to *buy*
   small-cap — so it cannot make an existing portfolio small-cap-heavy. That empty
   result is what let a downstream free-text surface hallucinate a deferred-work
   promise ("give me a moment, I'll come back with the plan").

The unifying observation: `tax/cash counterfactual`, `asset_class_tilt`, and a
market-cap ask are all one operation — *apply a preference → re-run → show
recommended vs. requested.* Phase 1 makes market-cap a first-class instance of that
spine and lets the engine-side preferences compose.

---

## 2. Scope (after audit)

### In — Phase 1

- **Market-cap target** (large / mid / small) as a real re-run dial (§5.1). *New; the headline.*
- **Compose engine-side preferences** — asset-class + market-cap + scenario (tax/cash)
  into one merged override dict, one engine run (§4). *Un-silos the engine-side lanes.*
- **Subgroup-aware fund count** — the count reshape preserves each subgroup's buy
  total, so it composes with a market-cap tilt instead of undoing it (§5.2). *New logic.*
- **Reply quality** — surface SEBI categories and present plans as tables (§7). *Prompt-only.*
- **ENGINE_VERSION bump** (§6.5).

### Deferred — later phases

- **`do_not_buy`** (fund-level buy exclusion) — feasible but has real edges
  (held-fund corpus invariant, sole-fund-in-subgroup stranding); not now.

### Dropped

- **`do_not_sell` / fund lock** — no changes to sell functionality. The engine has
  no per-fund lock and the current honest decline (`_LOCK_NOT_SUPPORTED`,
  [chat.py:563-569](../../../app/domains/rebalancing/services/rebal_engine/chat.py))
  stays. (A real lock is net-new sell-step work and would reverse a stated product
  stance — see §12; out of scope.)

### Non-goals (unchanged)

Positive "use fund X" force-buy; equity granularity beyond large/mid/small;
debt-duration or gold-vs-silver dials; sell-side trade-count limits.

---

## 3. The model

The target structure is one preferences object; **Phase 1 populates only the
engine-side fields** (marked ✅). The rest are placeholders for later phases.

```python
class RebalancePreferences:

    # ── TARGETS ── set a goal; the engine re-runs to hit it ──────────────
    asset_class_target: dict | None      # {equity, debt, others} abs %   ✅ exists
    market_cap_target:  dict | None      # {large, mid, small} abs %       ✅ new (Phase 1)

    # ── SCENARIO ── change the world the engine computes against ─────────
    effective_tax_rate:   float | None   # ✅ exists
    stcg_offset_budget:   float | None   # ✅ exists
    carryforward_st_loss: float | None   # ✅ exists
    carryforward_lt_loss: float | None   # ✅ exists
    additional_cash:      float | None   # ✅ exists (forces fresh AA)

    # ── CONSTRAINTS ── (post-engine reshape) ─────────────────────────────
    max_new_funds:      int  | None      # ✅ Phase 1 — now subgroup-aware (§5.2)
    exclude_categories: list[str]        # existing category exclusion
    do_not_buy:         list[str]        # deferred
    # do_not_sell — dropped (no sell-step changes)
```

`market_cap_target` is a split *within the equity beta sleeve* (see §5.1). It does
not change equity/debt/others totals — `asset_class_target` owns those. Both carry
**absolute** percentages; a no-number ask applies a documented default step (§6.2).

---

## 4. One path (engine-side compose, then existing reshape)

The precise shape is **one engine run, then the existing post-engine reshape stage**
— not literally one call. Targets and scenario are *engine inputs*; the count/
category consolidation is a *post-engine reshape* ([reshape_response](../../../AI_Agents/src/Rebalancing/consolidation.py),
called at chat.py:1311). Phase 1 unifies the **engine-side** half.

```python
prefs = detector.extract(question, snapshot, history)   # fills the object above

if prefs.is_empty():
    return answer_from_existing_plan()                  # READ — no engine run

overrides = build_override_dict(prefs)      # ONE dict: asset_class_tilt + market_cap_tilt
                                            # + tax/cash. (with_chat_overrides REPLACES,
                                            # so everything must be packed in once.)
baseline  = run_rebalancing(base_inputs)                        # recommended
requested = run_rebalancing(base_inputs + overrides,           # everything, one run
                            force_fresh_allocation = prefs.additional_cash is not None)
persist(requested, origin=CANDIDATE)                    # savable, firewalled
return comply_and_caution(baseline, requested, prefs)   # one reply shape
```

Two concrete fixes this requires (both from the audit, §12):
- The tilt handler today builds its own `{"asset_class_tilt": …}` and **ignores
  `action.overrides`** — so "raise equity AND set tax 20%" drops the tax key.
  `build_override_dict` must merge *all* engine-side keys.
- `with_chat_overrides` **replaces** rather than merges, so the combined dict must be
  assembled before the call, not layered.

Routing collapses to: **READ** (no preference set → answer about the existing plan),
or **COMPUTE** (any engine-side preference → the two-run path). The existing
`clarify`/`redirect` pre-checks stay, including the contradiction check (exclude a
category while asking for more of it). The count/category consolidation runs as the
post-engine reshape stage, now **subgroup-aware** so it composes with a market-cap
tilt (§5.2).

---

## 5. Engine plug points

### 5.1 Market-cap target (the new dial) — CONFIRMED feasible

Add `_apply_market_cap_tilt`, a sibling of `_apply_asset_class_tilt`
([pipeline.py:211](../../../AI_Agents/src/Rebalancing/pipeline.py)), run **after** the
asset-class rescale and **before** `_assign_subgroup_targets`, rescaling the equity
beta subgroups to the requested large/mid/small split while holding their combined
total fixed. The rest of the pipeline (caps, tax-aware sells, buys) already hits
whatever subgroup targets it is handed.

Granularity: **large → `low_beta_equities`**, **mid → `medium_beta_equities`** (also
multi/flexi/large-&-mid/aggressive-hybrid/dynamic/multi-asset), **small →
`high_beta_equities`** (also focused).

Audit-mandated correctness details:
- **Write the rescaled value to `.total`**, not `.long_term` — `_assign_subgroup_targets`
  keys off `.total` (pipeline.py:156); touching only `.long_term` is a silent no-op.
- **"Total equity" here = the low/med/high-beta sleeve only** (3 of 8 equity
  subgroups; value/sector/dividend/US and the frozen ELSS are untouched). This is a
  tilt *within the beta sleeve*, which matches the large/mid/small scope. Confirmed
  safe: the equity-subgroup slider runs strictly **upstream** (step 1) and is never
  re-run, so it cannot clobber the tilt (§12).
- **Composition order:** asset-class sets the equity pool; market-cap splits it.
  For a zero-current subgroup, follow the existing present-share precedent
  (pipeline.py:236-239).
- New override key `market_cap_tilt` added to the allow-list
  ([overrides.py:24](../../../app/domains/rebalancing/services/rebal_engine/overrides.py))
  and threaded through `input_builder` like `asset_class_tilt`. New field on
  `RebalancingComputeRequest` (`models.py`); `request_input` is JSON, so **no DB migration.**

### 5.2 Subgroup-aware fund count — NEW logic

The audit found the current reshape preserves only the grand-total buy and
redistributes **portfolio-wide**, tilt-unaware (§12) — so as-is it *can* pull money
out of a market-cap tilt. Phase 1 makes the count **subgroup-aware** so it composes:

- **Preserve each subgroup's buy total.** The reshape redistributes *within* each
  subgroup, never across — so a small-cap tilt's small-cap total is untouched.
- **Concentrate within a subgroup:** to hit a smaller fund count, keep the
  best-ranked fund(s) in each subgroup and fold the rest of that subgroup's budget
  into them.
- **Count floor = number of subgroups with buys.** "Max 4 funds" cannot go below one
  fund per bought subgroup without moving money across subgroups (which would break
  preservation). If the requested count is lower, **bump it up to the number of
  bought subgroups and disclose** — reusing the existing protected-category
  count-bump pattern (`consolidation.py` already bumps; the chat layer discloses).
- A count ask **without** a market-cap tilt still preserves subgroup totals — a
  strict improvement over today's portfolio-wide spread.

This replaces the `_reshape_legacy` flat portfolio-wide logic with subgroup-grouped
redistribution; `reshape_response` stays the post-engine entry point. Category
weighting / exclusion (existing) rides the same subgroup-grouped reshape.

---

## 6. Semantics & edge cases

### 6.1 Comply-and-caution (two runs)

Two engine runs per preference turn: baseline (`persist=False`) + requested
(`persist=True, origin=CANDIDATE`), then one reply. This is the proven asset-class
path ([chat.py:964](../../../app/domains/rebalancing/services/rebal_engine/chat.py))
generalised to market-cap. Notes from the audit:
- **Only persist a candidate when the plan actually changed** vs. baseline (avoid
  candidates identical to baseline).
- The `persist=True` branch also writes the telemetry row that becomes the next
  turn's conversational baseline (no origin filter). For **tilt** candidates this is
  acceptable (the customer is now discussing the plan they explored) — the same as
  today's asset-class behaviour. The persist/telemetry decoupling only becomes
  necessary when we add **constraint-only** candidates (deferred), so it is a
  follow-up, not Phase 1.
- Even `persist=False` can write an AA recommendation on a cold cache; the second
  run usually reuses the just-written cache. `force_fresh_allocation` is set only
  when `additional_cash` is present.

### 6.2 Market-cap default magnitudes

Relative to the sleeve's current level, always **upward**, never capped at 50%:
- **"more small cap"** (no number) → **+10% relative** (small-cap 20% of equity → 22%).
- **"small-cap heavy / mostly small cap"** → **+50% relative** (tunable), still
  upward; if already >50% it goes **higher still**. Feasibility-capped at 100%.
- **Zero-current** → a relative step can't grow from 0; **ask** ("you hold no small
  cap today — how much would you like?").
- **Increase impossible** (already near max) → **say so**, don't return an
  unchanged plan.

Recorded in `applied_preferences` and disclosed in the reply.

### 6.3 Read-vs-compute detection

A question *about* existing numbers = READ (no preference fields set). An instruction
*to change* the plan sets ≥1 field → COMPUTE. Keeps the current narrate/educate vs.
action signal, expressed as "did any engine-side preference get set?".

### 6.4 Persistence and Save

The requested (tilted/scenario) plan persists as a **candidate** (`ORIGIN_CANDIDATE`),
firewalled from committed reads until Saved ([saved_plan_service.py](../../../app/domains/rebalancing/services/saved_plan_service.py)).
Phase-1 candidates are engine-side only, so `request_input` (JSON) captures them
faithfully — no fidelity gap. (Pure assumption what-ifs — tax rate, offset budget,
carry-forward — are shown but **not** persisted.)

### 6.5 ENGINE_VERSION

Market-cap tilt is output-altering, so **bump `ENGINE_VERSION`**
([config.py:106](../../../AI_Agents/src/Rebalancing/config.py), currently `1.5.0`) —
it is stamped into response metadata and persisted for cache/repro tracking
(CLAUDE.md rule).

---

## 7. Reply / formatter (SEBI categories + tables) — CONFIRMED prompt-only

The data already exists: `build_rebal_facts_pack` carries `sub_category` (the SEBI
label) on every `buckets[]` and `fund_actions[]`
([service.py:310-326](../../../app/domains/rebalancing/services/rebal_engine/service.py)),
flagged "customer-facing label; copy verbatim". Even the deterministic fallback
already renders a per-SEBI-category table. The fix is prompt-side only:

- Present the plan **by SEBI category** (a table: one row per `sub_category`,
  columns *Current → Buy → Sell → Planned*, with a totals row), plus a compact
  fund-level table for the largest buys/sells.
- Never surface `asset_subgroup` (internal `low_/medium_/high_beta`).
- Per §5.1's coarseness, when a market-cap tilt moved a shared subgroup, add one
  light line ("this also nudges your flexi/multi-cap funds in the same bucket")
  rather than implying pin-point precision.

The comply-and-caution reply also states recommended-vs-requested mix (verbatim, no
invented middle-ground), the requested large/mid/small split, per-fund buy changes
(the delta), the tax, and one grounded caution.

---

## 8. Anti-stall guardrail (small, defence-in-depth)

Independent of routing: add a synchronous-execution hard rule to the shared persona
([persona.py `SHARED_MECHANICS`](../../../AI_Agents/src/persona.py)) — PI answers in
one synchronous turn and never claims to be "working on it", "coming back", or doing
background work; if it lacks a result it says so. The Phase-1 tilt removes the
*trigger*; this backstops any other gap. One line + a guard test in the style of the
existing temperature-pinned / no-internal-jargon scans.

---

## 9. Build order (Phase 1)

1. **`market_cap_tilt`** — request field (`models.py`), allow-list key
   (`overrides.py`), input-builder threading, and `_apply_market_cap_tilt` in the
   pipeline (write to `.total`; beta-sleeve rescale; zero-current handling). Bump
   `ENGINE_VERSION`. Unit-test the rescale + composition with asset-class.
2. **Compose engine-side overrides** — `build_override_dict` merges asset-class +
   market-cap + tax/cash into one dict; fix the tilt handler dropping
   `action.overrides`; set `force_fresh_allocation` on cash. Detector: extract a
   market-cap target (large/mid/small + magnitude).
3. **Subgroup-aware count** — replace the flat `_reshape_legacy` with
   subgroup-grouped redistribution: preserve each subgroup's buy total, concentrate
   within subgroup, bump the count up to #bought-subgroups and disclose. Composes
   with the market-cap tilt.
4. **SEBI-category tables** — formatter body prompt.
5. **Anti-stall guardrail** — persona line + guard test.

Deferred phases: `do_not_buy`. Dropped: `do_not_sell`.

---

## 10. Decisions (resolved 2026-08-30)

1. Market-cap step: "more" = +10% rel; "heavy/mostly" = +50% rel, always upward,
   uncapped at 50%, 100%-feasibility-capped; zero-current → ask; impossible → say so.
2. Two engine runs on every preference turn.
3. Savable = plan-shape changes (targets, additional cash); pure assumption
   what-ifs = ephemeral. Phase-1 candidates are engine-side, so `request_input`
   captures them faithfully.
4. Fund count decides number of funds only; made **subgroup-aware** this phase
   (preserves each subgroup's buy total; count floor = #bought subgroups, disclosed).
5. Coarseness disclosed lightly.
6. Reply surfaces SEBI categories and uses tables.
7. `do_not_sell` dropped (no sell-step changes); `do_not_buy` deferred;
   subgroup-aware count **in this version**.

---

## 11. Testing

- **Unit:** market-cap rescale math (writes `.total`; holds beta-sleeve total fixed);
  asset-class + market-cap composition; merged override dict carries tax/cash
  alongside a tilt; zero-current → ask path.
- **Detector eval:** extend `AI_Agents/tests/test_rebal_detector_eval.py` — a
  market-cap ask fills `market_cap_target`; a tilt+count ask sets both.
- **Unit (count):** subgroup-aware reshape preserves each subgroup's buy total;
  concentrates within subgroup; bumps the count up to #bought-subgroups and flags it.
- **E2E:** `"more small cap"` produces a real re-run plan (sells + buys + tax) with a
  SEBI-category table; `"only equity, more mid cap"` composes both tilts; `"only
  equity, more mid cap, max 4 funds"` composes both tilts **and** the subgroup-aware
  count (each subgroup's buy total preserved; count floor disclosed if it bumps).
- **Guardrail:** persona scan test asserts the synchronous-execution rule is present.
- **Test-env gotchas:** letter-bearing UUIDs (all-digit UUIDs coerce to float on
  sqlite); create only the table(s) under test (`Base.metadata.create_all` fails on
  the Postgres `ARRAY` column).

---

## 12. Audit findings (load-bearing facts, 2026-08-30)

Corrections the plan-writer/implementer must respect:

- **Market-cap seam is safe.** `aggregated_subgroups` exposes low/med/high-beta as
  individually-addressable rows; `_assign_subgroup_targets` reads per-subgroup
  `.total`; the equity-subgroup slider runs upstream (step 1) and is never re-run.
  **Write to `.total`.**
- **`protected_floor_inr` does NOT stop sells.** It is read only by step 1 (cap
  raise), never by step4/step5. Force-exit and low-rated funds are liquidated
  regardless. A real lock would be net-new sell-step work + reverse
  `_LOCK_NOT_SUPPORTED`. → **`do_not_sell` dropped.**
- **The count reshape is portfolio-wide and tilt-unaware** (`consolidation.py`:
  grand-total only, flat `_reshape_legacy`, pro-rata across all survivors). It *can*
  pull money out of a tilt. → build the **subgroup-aware** reshape (preserve
  per-subgroup totals; count floor = #bought subgroups) this phase.
- **`do_not_buy` edges:** dropping a *held* fund's row corrupts the corpus base (must
  zero the target instead); a sole-ranked-fund-in-subgroup strands that subgroup's
  budget. → deferred.
- **Detector already composes; barrier is handler ordering.** But `with_chat_overrides`
  **replaces** (not merges) and the tilt handler ignores `action.overrides`. → build
  one merged dict.
- **SEBI tables are prompt-only** (data already present). **ENGINE_VERSION** exists
  and must bump; candidate `origin` column already exists → **no migration.**
