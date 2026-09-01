# Investment Preferences — Additional Investment (SIP + Lump-sum) — Design Spec

**Date:** 2026-08-27
**Status:** Approved (design); implementation not started. **Staff-engineer audit folded in 2026-08-27** — see "Audit corrections" below.
**Flows in scope:** SIP + lump-sum only (`app/domains/additional_investment/`)
**Companion to:** `2026-08-24-investment-preferences-design.md` (this is that spec's Sequencing step 2, "SIP/lump-sum parity via the shared layer") — reconciled with the divergences the rebalancing build actually shipped (see "Reconciliation").
**Not in scope:** persisting a recommendation into the customer's portfolio — a separate problem (rebalancing-first), tracked on its own.

## Audit corrections (folded in 2026-08-27)

The first draft anchored the tilt to "the recommended DEPLOYMENT mix." An audit showed that is wrong for lump-sum three ways (semantic, mechanical, and as a claimed mirror of rebalancing), because that deployment is a **deficit-fill** flow that is lopsided and often zero in the tilted class. Corrections now in the spec:

- **Tilt baseline = the IDEAL deployment mix, not the deficit-fill.** A tilt turn **replaces** deficit-fill with ideal-proportional deployment (details in Apply). Caller-side only; `normalize_tilt` is unchanged.
- **Two-run is factored at the engine, not the service** — prime PAA + snapshot once, run the pure engine twice. "Negligible cost" is only true this way.
- **The formatter edits are SUBTRACTIVE** — the existing "category picks are information" directive appears in two bodies and must be removed/conditionalised, not just "extended."
- **Routing is verified, not assumed** — cold-start named-fund and follow-up tilt asks are added to the classifier guard set; a known cold-start gap is documented.
- **"Parity" reframed** — parity of *vocabulary + comply-and-caution posture*, NOT of reachable portfolio mix (fresh money can only move the total so far; that limit is the whole point of the deployment-vs-total caution).

## Problem

Customers state preferences when deploying fresh money — "put it only into equity funds", "more into gold", "focus on large cap", "no lock-in funds", "why not fund X?" — and additional-investment today either ignores them or treats them as narration. Its one existing preference, `focus_category`, is **informational only**: extracted, canonicalised, narrated, stamped as audit metadata, but it **never changes the allocation** (the prompts insist "the plan is the recommendation; category picks are information", `ainv_engine/chat.py:293-295`). No comply-and-caution, no deviation lens, no tilt.

Rebalancing has the full mechanism (shipped this cycle). This spec brings the **same vocabulary and comply-and-caution posture** to SIP and lump-sum. (It does NOT and cannot bring the same *reachable mix* — see Decision 1.)

## Reconciliation with the 2026-08-24 spec

The rebalancing build diverged from the older design; the as-built is the source of truth:

- **No band-edge magnitude default.** Removed (it could land below the recommended plan and read as "more equity → less equity"). Shipped default is a fixed **+10pp** step (`investment_preferences.py::DEFAULT_TILT_STEP_PP`).
- **Named-fund why-not shipped; inclusion still deferred** (Phase-2 input-builder seam). Same defer here.
- **Tilt-delta framing shipped** — cautions show the per-fund *change vs the recommendation* (`_buy_changes_vs_recommended`), not absolute buys. Ported.
- **`pure_equity_only` shipped** — "100% / only equity" drops the hybrid subgroup so the mix lands ≈100%, not ≈85%. Ported (subgroup named below).
- **Ideal↔target bridge** (rebalancing narrates the on-paper ideal vs the tax-constrained landing). The additional-investment analog is the **deployment-vs-total** caution for lump-sum.

## Decisions (locked with product owner)

1. **Parity of vocabulary + comply-and-caution posture — NOT of reachable mix.** Rebalancing's tilt re-runs allocation against tilted targets and **sells** to get there, so it can reach any mix. The additional-investment tilt only re-weights *fresh money* across deployment targets — it can never move the customer's *total* mix beyond what the deploy amount buys. That gap is honest and is exactly what the deployment-vs-total caution discloses. Implementers must not chase a "same landing mix."
2. **Widen the extractor — do NOT add a follow-up detector.** Rebalancing needs a mode-classifier because it acts on a *persisted* run. Additional-investment is **write-once**: each turn recomputes from scratch, so there is nothing to branch on. Widen the single-pass `extract_deploy_request` (`ainv_engine/chat.py:127-219`) to carry the preference fields alongside amount/cadence. `handle()` stays linear: *extract → apply preferences → compute (twice if a preference binds) → format*. No new LLM call, no mode dispatch.
3. **Comply-and-caution, unbounded; never silently ignore.** Any expressible preference is honoured; the deviation-vs-recommendation contrast is always present; an expressed-but-unservable preference says so before the plan and fires the PostHog unserved-preference event (turn_id + failure_class + canonical resolver output only — never raw chat text).
4. **Per-turn stateless.** Recomputed each turn from the current message; nothing new persists beyond recording the applied preferences + magnitude defaults in the **existing** run audit trail. Saving a recommendation into the portfolio is out of scope.

## Existing machinery (reuse, don't rebuild)

**Shared layer (already in `mutual_funds/services/`, built for rebalancing):**
- `investment_preferences.py::normalize_tilt(current_mix_pct, scope_only, tilt_asset_class, tilt_delta_pp, tilt_target_pct) → TiltResult(mix_pct, default_step_applied)`. Pure, engine-agnostic, 3-class, **baseline-agnostic** (the caller passes the mix). This spec is its first additional-investment importer.
- `category_resolver.py::resolve_category(-ies)` — free text → ranking `sub_category`; unranked → `None`. Additional-investment uses the singular today; needs the plural for multi-category asks.
- `fund_ranking_lookup.py::resolve_ranked_fund` + `get_rejection_reasons` — named fund → recommended / rejected / ambiguous / unknown. Not yet used here.

**Additional-investment's own bits:**
- Extractor `extract_deploy_request` / `_DEPLOY_EXTRACT_SYSTEM` (`ainv_engine/chat.py:127-219`) — widen it.
- Three formatter bodies: `_AINV_FORMATTER_BODY` (SIP), `_AINV_DEFICIT_FORMATTER_BODY` (lump-sum), `_AINV_CATEGORY_PROBE_BODY`.
- Engine: `AI_Agents/src/additional_investment/ratio.py` (`compute_deficit_targets` lump-sum, `compute_targets` SIP) → `SubgroupTarget` list; `selection.py::select_funds` / `select_funds_sip` (per-fund, `sub_category` in hand); `run_additional_investment` (pipeline) is the pure engine; `compute_practical_allocation_result` (PAA) + `load_holdings_snapshot` are the expensive priming steps in `service.py`.
- Already shared with rebalancing: `fund_rank.get_fund_ranking`, PAA, `Rebalancing.tables`/`config` caps, `latest_buy_trades_by_subgroup`.

## Vocabulary (v1)

Same fields as rebalancing's `RebalanceAction`, added to the widened extractor's output model.

| Field | Customer utterance | Semantics (additional-investment) |
|---|---|---|
| `scope_only_asset_classes` | "only equity funds", "no gold" | `normalize_tilt` pins the excluded classes to 0 and the allowed class(es) absorb 100% — **immune to the baseline question below** (it pins, not steps) |
| `tilt_asset_class` + `tilt_delta_pp`/`tilt_target_pct` | "more equity", "to 70%" | Signed pp delta **or** absolute %; **baseline is the IDEAL deployment mix** (post-investment PAA ideal), never deficit-fill and never the portfolio target; other classes renormalised pro-rata; no number → +10pp |
| `pure_equity_only` (internal) | "100% / only equity" | Drop the **`multi_asset`** subgroup (`models.py:83`) from the equity sleeve via `exclude_subgroups` so deployment lands ≈100% equity |
| `allowed_categories` / `excluded_categories` | "only large & mid cap", "no lock-in" | Eligibility filter on the buy sleeve (SEBI `sub_category`) |
| `category_weights` | "more mid cap" | Minimum share of the buy budget for the category; no number → 10% (v1 constant, disclosed) |
| `target_fund_count` | "just 4 funds" | Cap on the number of new-buy funds |
| `named_fund` + `named_fund_intent` | "use fund X" / "why not fund X?" | why_not → `get_rejection_reasons()` / selection reason; unranked → honest no-rank; include → **deferred** (honest "coming later" + `named_include_deferred` telemetry) |

`focus_category` (info-only today) is **subsumed when a deploy amount is present**: with an amount, a named category becomes a `category_weights` entry that actually reshapes the buys. The **no-amount `category_probe` path is unchanged** — a category before any amount still asks for the amount and shows info; it binds as a weight only once an amount arrives. All category strings pass through `resolve_category(-ies)`. Dropped by product owner: AMC exclusion/preference/cap, per-fund minimum amounts.

## Architecture

### Extraction (widen, no detector)
`extract_deploy_request` gains the preference fields in one Haiku call, same prompt shape as rebalancing's whitelist ("unknown dimension → honest redirect"; multiple keys compose). **Step-1 caveat:** adding fields/examples to a Haiku prompt that also emits amount/cadence can move the existing labels — so this is behaviour-neutral only once the extractor eval proves amount/cadence didn't regress (baseline first). The regex fallback stays for amount/cadence only.

### Apply (deterministic, ALL BUYS — no sells)

**Pre-engine — tilts/scope (incl. `pure_equity_only`):**
- The tilt baseline is the **ideal deployment mix** — the equity/debt/others rollup of the eligible subgroups' `total` column (`SubgroupBucketAmounts.total`, the post-investment PAA ideal already in the engine input, `models.py:44`). NOT the deficit-fill flow.
- Pass that ideal mix to `normalize_tilt`; then **a tilt turn REPLACES deficit-fill** — deploy the tilted class shares across each class's subgroups **by their ideal `total` proportions** (not by deficits). This (i) makes "+10pp equity" mean "deploy to a 70/20/10 split," (ii) always has equity subgroup targets to populate (deficit-fill emits none for a class with no deficit — `ratio.py:113-115` — which is why scaling the deficit targets is undefined and must not be used), (iii) leaves deficit-fill as the untouched **default** (no-preference) path.
- **Why lump-sum needs this specifically:** lump-sum is *always* deficit-fill in production (holdings snapshot is loaded unconditionally, CAMS mandatory — `service.py:183-184`), so the lopsided-baseline case is the common case, not an edge. The no-holdings lump-sum path is unreachable in chat today — do not design for it.
- **SIP is not immune either:** a short-term-goal SIP weights toward debt/liquid subgroups, so its recommended mix can also be ~0% equity. The ideal-mix baseline is therefore applied **uniformly across both cadences**.
- `scope_only` ("only equity") is immune to all of the above — it pins classes to 0/100 regardless of baseline. Add `asset_class_tilt` / `pure_equity_only` to `AdditionalInvestmentInput`.
- Because it is all-buys, a tilt never triggers sells and never realises tax — the big simplification vs rebalancing (no STCG/exit-load, no sell-preservation invariant, no twice-for-tax).

**Post-engine — category reshape:** a small set of functions (not a heavy module) on the buy sleeve — "meet each requested category's minimum share, then distribute the remainder pro-rata to survivors" — for allowed / excluded / `category_weights` / `target_fund_count`. Invariants scoped to this reshape: **total deploy preserved exactly**, rounding residual to the largest buy, count-trim must not evict a weight-target category, identity when nothing binds.

**Composition order** for stacked preferences: (1) tilt/scope pre-engine → (2) eligibility filters → (3) category weights → (4) fund count. Cross-key contradictions ("only debt, and more mid cap") → honest clarify naming the conflict. Named-fund inclusion stays the deferred input-builder seam; why-not answers now.

### Comply-and-caution (two-run, factored correctly)
When a preference binds, produce two deployments — a **recommended** baseline and the **requested** (preferences applied). **Prime PAA + the holdings snapshot ONCE**, then call the pure `run_additional_investment` twice (baseline targets, tilted/reshaped targets) — do NOT call `compute_additional_investment_result` twice (that re-runs PAA + snapshot, the actual cost; mirrors the 2026-08-24 spec's "engine runs once unconstrained"). Build a `constraint_impact` carrying `recommended_deploy_mix_pct` vs `requested_deploy_mix_pct` + `buy_changes_vs_recommended` (per-fund "+₹X vs our recommendation").

### Caution framing differs by cadence
- **Lump-sum** deploys to fill deficits toward the ideal, so:
  - **Deficit override:** tilting toward a class already at/above ideal (e.g. "all equity" at 95% equity) means the recommended plan would have filled the *debt* gap; comply, but the caution names it.
  - **Deployment-vs-total:** show where the **total portfolio lands after** (current + deploy) — e.g. "₹25L all-equity moves your total from 95% to 96%." This is the **antidote to a small deployment reading as a big move**, and cheap (`by_subgroup` snapshot + deploy split are in hand). Keep it in v1; do not defer.
- **SIP** is a recurring flow — no single "total after," so the caution is **monthly-mix deviation** ("your SIP now goes 100% to equity vs our recommended 60/30/10").
- **Either cadence — undeployment from a tilt:** concentrating the whole deploy into one class can exceed that class's eligible funds × per-fund caps (`selection.py:26-35`), spiking `undeployed_inr`. When a *requested* tilt raises undeployment, the caution must say so ("you asked for all-equity; ₹X couldn't be placed under concentration caps") — never a silent shortfall on a plan the customer asked for.

### Magnitude defaults (policy table — never LLM judgment)
| Case | Rule |
|---|---|
| Number stated ("by 10", "to 70%") | Use it (delta or absolute, normalised) |
| Tilt, no number | **+10 pp** from the **ideal** deployment mix (`DEFAULT_TILT_STEP_PP`); reply names it, invites a number |
| Category weight, no number ("more mid cap") | Raise the category to **10%** of the deploy budget (v1 constant, disclosed) |
| Vague quantifiers ("a bit more") | Treated as no-number in v1 |

### Prompt rewrites (SUBTRACTIVE — enumerate, don't just "extend")
The existing bodies carry directives that **contradict** the new reshape and must be removed/conditionalised on the amount-present path:
- `_AINV_FORMATTER_BODY` (SIP) `chat.py:291-295`: "NEVER offer to execute a category-only deployment … category picks are information" — **remove/conditionalise.**
- `_AINV_DEFICIT_FORMATTER_BODY` (lump-sum) `chat.py:381-385`: same block verbatim — **remove/conditionalise.**
- `_AINV_CATEGORY_PROBE_BODY` **keeps** the info-only caveat — the no-amount path is genuinely unchanged.
Otherwise a single reply will reshape the plan *and* claim category picks don't change it.

### Named-fund (why-not now; a cold-start caveat)
why-not / why-buy via `resolve_ranked_fund` + `get_rejection_reasons`, mirroring rebalancing. **Cold-start gap (documented, acceptable):** a first-turn "why didn't you pick fund X for my new money?" with no active plan routes to `mutual_fund_query` (the classifier holds a fund ask in the active intent only *while a plan is active*, else `mutual_fund_query`), and — because additional-investment is write-once with no follow-up detector — its why-not path only runs when the turn is already in `additional_investment`. `mutual_fund_query` answers why-not from the same ranking, so the customer is served; parity is mid-conversation, not cold-start.

### Out-of-vocabulary / honest declines
Category in the ranking → in-vocabulary automatically. Unranked concept (ESG, international) → honest "we don't rank there"; plan not recomputed. Unmodelled dimension (AMC, direct/regular, holding locks) → honest redirect. All non-served cases fire the PostHog unserved-preference event.

### Counsel, don't comply
IDCW / regular-plan asks → educate mode using the ranking's `div_growth_reason` / `direct_regular_reason`, same as rebalancing.

### Audit trail
Every run under active preferences records the full preference payload **and which magnitude defaults were applied** (customer number vs +10pp vs fixed category weight), in the existing run audit metadata. A plan on record must be reconstructible.

## Error handling
- Unresolvable category → honest clarify / no-rank.
- Empty eligible set after scoping → honest no-op error (never a silent unconstrained plan).
- Category weight unmeetable → honest shortfall, remainder distributed normally.
- Degenerate tilt (class ≤ 0) → clamp to zero, renormalise, caution states it.
- Tilt-induced undeployment → surfaced in the caution (above), not just `undeployed_inr`.

## Testing
- Reshape/tilt math: unit tests mirroring rebalancing's consolidation suite — identity, deploy-conservation, rounding-residual, weight-target satisfaction, exclusion/inclusion, `pure_equity_only` (multi_asset dropped), and specifically **tilt from an ideal mix where the tilted class has zero deficit** (the case that broke the first draft).
- Extractor: labelled examples per new field + magnitude-default cases + stacked composition + contradictions, in the existing eval harness. **Baseline the current extractor first** (amount/cadence must not regress — this gates Sequencing step 1).
- Integration: per cadence, assert `constraint_impact` present when a preference binds, honest-limitation sentence present when a preference can't be applied, and — lump-sum — the deployment-vs-total figure present on a tilt turn.
- Routing: **verify, don't assume.** Add to the classifier guard set additional-investment-context **tilt and named-fund** utterances, both cold-start and follow-up (not just the "only equity"/"large cap" scope cases already covered), and confirm the no-amount `category_probe` turn persists `active_intent=additional_investment` so the next turn sticks.

## Out of scope (v1)
- Persisting a recommendation into the portfolio (separate problem, rebalancing-first).
- Named-fund inclusion/substitution (Phase-2 input-builder seam).
- Agentic loop, tranche/STP deployment, inverse search, taxonomy changes.
- v2 candidates (unserved-ask telemetry prioritises): theme-level sectoral picks.

## Sequencing
Ordering couplings are real — this is not five independently-shippable slices:
1. **Widen the extractor + shared-layer imports.** Behaviour-neutral **only after** the extractor regression baseline passes (amount/cadence stable).
2–4. **Ship the reshape as ONE flag-gated unit:** pre-engine tilt (ideal-mix baseline; `asset_class_tilt`/`pure_equity_only` on the input) + two-run `constraint_impact` + post-engine category reshape (subsumes info-only `focus_category`) + the **subtractive** formatter rewrite. Shipping any of these without the formatter rewrite yields a self-contradicting reply. Step 2 also has a **design dependency**: the ideal-mix baseline (Audit correction #1) must be settled before it is written.
5. **Named-fund why-not — independently shippable first slice** (pure `resolve_ranked_fund` + `get_rejection_reasons`, no reshape, no formatter contradiction). Low-risk early win.
6. Transcript + telemetry review decides v2.
