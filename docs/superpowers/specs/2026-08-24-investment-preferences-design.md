# Investment Preferences — Design Spec

**Date:** 2026-08-24
**Status:** Approved (design); implementation not started
**Flows in scope:** Rebalancing, SIP, lump-sum (`app/domains/rebalancing/`, `app/domains/additional_investment/`)

## Problem

Customers state investment preferences in chat — "only equity funds", "increase my equity exposure", "more mid cap", "nothing with a lock-in", "why not fund X?" — and today Prozpr either ignores the preference or redirects with "we can't do that". The rebalancing flow already contains a narrow, correct mechanism for a subset of this (the F3-B consolidation constraints); the other flows have nothing. We widen that mechanism's vocabulary and extend it to all three flows.

## Decisions (locked with product owner)

1. **Deterministic single pass — no agentic loop.** One extraction call → whitelisted vocabulary → pure-Python apply → one formatter call. The LLM extracts and narrates; it never decides magnitudes or whether computation happens. The loop remains a documented growth path only; everything built here is also the toolbox a future loop would use.
2. **Comply-and-caution, unbounded.** Any expressible preference is honored, including beyond the customer's risk band. The deviation-vs-recommendation comparison is structurally always present — the customer decides, informed. No suitability clamp, no refusal tier.
3. **Never silently ignore.** If a preference was expressed and cannot be applied, the reply says so explicitly before presenting any plan. Every unserved ask fires a PostHog telemetry event (raw ask + failure class); vocabulary grows from that report, not guesswork.
4. **Per-turn stateless.** Nothing persists, matching today's consolidation constraints. "Back to the full plan" is narrate mode. Persistence (session or standing) is a deferred product decision.

## Existing machinery (reuse, don't rebuild)

- Detect: `app/domains/rebalancing/services/rebal_engine/chat.py` (`_detect_rebal_action`, modes incl. `counterfactual_explore`, `consolidate`, `clarify`, `redirect`); SIP/lump-sum mirror in `app/domains/additional_investment/services/ainv_engine/chat.py`.
- Canonicalize: `app/domains/mutual_funds/services/category_resolver.py` — free text → ranking `sub_category`; unranked asks stay `None` (honest "we don't rank there").
- Apply: `AI_Agents/src/Rebalancing/consolidation.py` — `ConsolidationConstraints`, `compute_reshaped_buys` (engine runs once unconstrained; buy budget redistributed; sells and total preserved; identity when nothing binds).
- Compare: `app/domains/rebalancing/services/rebal_engine/constraint_impact.py` — two-lens comply-and-caution deviation.
- Explain rejections: `app/domains/rebalancing/services/rebal_engine/fund_rank.py` `get_rejection_reasons()` — `{isin: reason}` for evaluated-but-rejected funds from the ranking CSV.
- SIP/lump-sum plan shape: `additional_investment_module_service.py` runs practical allocation inline, deploys fresh money per subgroup — an all-buys plan, so the same reshape applies with no sell-preservation complexity.

## Vocabulary (v1)

One shared frozen model, `InvestmentPreferences`, in `app/domains/mutual_funds/services/` (beside `category_resolver.py` — both flows already depend on this domain; avoids agent-peer imports):

| Field | Customer utterance | Semantics |
|---|---|---|
| `asset_class_scope` | "only equity funds", "no gold" | Asset-class allowlist applied to targets before the engine |
| `allocation_tilt` | "increase my equity exposure (by 10 / to 70%)" | Signed pp delta **or** absolute target %, normalized internally to one representation; other classes renormalized pro-rata |
| `category_weight_targets` | "more mid cap" | Minimum shares of the buy sleeve, SEBI `sub_category` vocabulary |
| `excluded_categories` | "nothing with a lock-in" (ELSS) | Inverse of `allowed_categories` |
| `named_fund` | "use fund X" / "why not fund X?" | Recommended → include in buy plan; evaluated-and-rejected → answer with `get_rejection_reasons()`; unranked → honest no-rank |
| `allowed_categories` | (existing F3-B) | Unchanged |
| `target_fund_count` | (existing F3-B) | Unchanged |

All category strings pass through `resolve_category`. Considered and dropped by product owner: AMC exclusion/preference/cap, per-fund minimum amounts — telemetry can reopen them.

## Architecture

### Detection
Both existing detectors gain the new keys in their whitelists; same prompt shape, same "unknown key → redirect" rule, multiple keys per turn compose into one action ("only equity, and more mid cap"). No new LLM calls.

### Apply (deterministic, two seams)
- **Before the engine** — `asset_class_scope`, `allocation_tilt`: adjust the target mix (rebalancing) or per-subgroup deployment targets (SIP/lump-sum, after practical allocation runs); renormalize pro-rata. Arithmetic bounds only (mix stays 0–100).
- **After the engine** — `allowed_categories`, `excluded_categories`, `category_weight_targets`, `target_fund_count`, `named_fund` include: generalize `compute_reshaped_buys` from drop-and-redistribute to "meet each requested category's minimum share, then distribute the remainder pro-rata to survivors". Invariants unchanged: total buy preserved exactly, sells never touched, rounding residual to the largest buy, identity when nothing binds.
- Taxonomy: customers speak cap/sector language; the allocation engine thinks in beta buckets (`high/medium/low_beta_equities`). Translation happens only at fund selection, where every `BuyCandidate` already carries `sub_category`. **No taxonomy changes.**

### Magnitude defaults (policy table — never LLM judgment)
The detector extracts a number or the absence of one; this table decides the rest; the formatter always discloses what was assumed:

| Case | Rule |
|---|---|
| Number stated ("by 10", "to 70%") | Use it (delta or absolute, normalized) |
| Tilt, no number | Default to the customer's **risk-band edge**; reply names it and invites a number |
| Tilt, no number, already at band edge | Fixed +5 pp beyond the edge; caution lens explains why our recommendation sits where it does |
| Category weight, no number ("more mid cap") | Fixed +10 pp of the equity buy sleeve toward the named category, capped at the sleeve. v1 constant, tuned from transcripts |
| Vague quantifiers ("a bit more") | Treated as no-number in v1 |

Every plan's tilt is traceable to a customer number or a documented default.

### Comparison & narration
`build_constraint_impact` gains a third lens — requested-vs-recommended target mix — so tilts (which the buy-mix lens can miss) always show their cost. Facts pack carries both plans; the formatter narrates comply-and-caution. No new narration machinery.

### Out-of-vocabulary behavior
- Category exists in the ranking → in-vocabulary automatically (`resolve_category` is data-driven from the live CSV).
- Concept we don't rank (ESG, international) → honest "we don't rank funds there"; plan not recomputed.
- Dimension not modeled (AMC, direct/regular, specific-holding locks) → existing `redirect` with reason.
- All three of the non-served cases fire the PostHog unserved-preference event.
- Enforcement of "never silently ignore": preference-shaped content detected + no override emitted ⇒ the honest-limitation sentence is required in the facts pack.

### Counsel, don't comply (deliberate non-vocabulary)
Dividend (IDCW) option and regular plans: respond in educate mode using the ranking's own `div_growth_reason` / `direct_regular_reason` content. Building compliance here would worsen plans to honor a misconception.

## Error handling

- Unresolvable category → existing clarify / honest-no-rank reply.
- Empty eligible set after scoping → existing honest no-op error (never a silent unconstrained plan).
- Category weight unmeetable (no recommended fund ranked there) → honest shortfall statement, remainder distributed normally.
- Degenerate tilt (class driven ≤ 0) → clamp to zero, renormalize, impact lens states it plainly.

## Testing

- Reshape/tilt math: unit tests mirroring the existing consolidation suite — identity, conservation, rounding-residual placement, weight-target satisfaction, exclusion/inclusion filters.
- Detectors: labeled examples per new key (in-vocabulary, each fallback class, each magnitude-default case) added to the existing eval harness (`AI_Agents/tests/_eval_harness.py`). Routing stays a classification eval by design. The question set is a separate deliverable, designed with the product owner before implementation of the detector changes.
- Integration: one test per flow asserting `constraint_impact` present whenever preferences are active, and the honest-limitation sentence present whenever a preference could not be applied.

## Out of scope (v1)

- Agentic loop (growth path only), any persistence, cross-flow composition, inverse search ("max mid-cap within my band"), taxonomy changes.
- **v2 candidates, priority set by unserved-ask telemetry:** sell-side locks ("don't sell fund X" — breaks the sells-untouched invariant; needs its own design with `tax_aging`), tax-shaped sell filters ("don't book STCG"), tranche/STP deployment, theme-level sectoral picks (needs scheme-level theme tagging).

## Sequencing

1. Rebalancing (vocabulary growth on existing machinery).
2. SIP/lump-sum parity via the shared layer.
3. Transcript + telemetry review decides v2.
