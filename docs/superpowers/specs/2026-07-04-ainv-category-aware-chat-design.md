# Category-aware additional-investment chat — design

**Date:** 2026-07-04
**Status:** approved direction; pending implementation plan
**Scope:** `app/domains/additional_investment/services/ainv_engine/` (chat + extractor layer only — NO engine change, NO schema change)
**Depends on:** deficit-fill (spec 2026-07-03, shipped) — `outcome.deficit_facts` supplies the ideal-vs-current numbers this feature narrates.

## Problem

When a customer asks for a specific fund category ("which smallcap fund should I
buy", "I want smallcap only"), the word "smallcap" is extracted nowhere and
reaches nothing. Observed transcript failure (2026-07-02): the bot silently
returned a diversified plan, then — on the "smallcap only" follow-up — the
formatter hallucinated a capability ("let me fetch a pure smallcap lineup…")
that does not exist, a dead end.

## Decision

> Answer the literal question honestly: name the TOP-RANKED funds in the asked
> category, always paired with the caveat that the goal-based plan is what we
> recommend — and when an amount is known, run the REAL deficit-fill deployment
> and state plainly where that category stands in it.

Decisions locked with the product owner:

| Decision | Choice |
| --- | --- |
| Category + amount precedence | Run the real (deficit-fill) plan AND surface top category funds + caveat — never a category-concentrated plan (2026-07-02) |
| Category coverage | ALL `sub_category` values present in the fund ranking — no curated subset (2026-07-02) |
| Follow-up amounts | Extractor reads RECENT CONVERSATION HISTORY: a category-only follow-up reuses the amount/cadence given earlier; the CURRENT message wins on conflict (2026-07-04) |

## Behavior (four cases)

| Customer message | Reply |
| --- | --- |
| category + amount | Real deployment (deficit-fill for lumpsum / legacy for SIP) + top category funds + caveat + the category's honest status (below) |
| category, no amount (incl. in history) | Top category funds + caveat + ask amount & cadence |
| amount, no category | Today's plan — unchanged |
| neither | Today's ask-amount — unchanged |

### The category-status story (deficit-aware — three states + one policy variant)

Where does the asked category stand in the plan? `deficit_facts` +
`buys[].sub_category` decide, and the reply must say it plainly:

1. **`in_plan`** — a buy's `sub_category` == asked category → "the plan already
   gives you smallcap: Nippon ₹48,900."
2. **`subgroup_funded_other_funds`** — the category's subgroup received money
   but via other categories (smallcap and midcap share `high_beta_equities`) →
   "your growth-equity gap gets ₹X via midcap; if you want it smallcap-specific,
   our top picks are …"
3. **`at_or_above_ideal`** — the category's subgroup has no gap (current ≥
   ideal) → "you already hold more of this than your ideal calls for, so this
   deployment adds none — here are our top-ranked picks anyway, but we'd caution
   against overweighting further." *(The strongest advice moment — say it.)*
4. **`excluded_by_policy`** — the category's subgroup is in
   `exclude_subgroups` (ELSS `tax_efficient_equities`, direct stocks) → "we rank
   these ELSS funds, but fresh chat deployments never buy ELSS (3-year lock-in
   policy)" — funds still named, policy stated.

SIP runs have no `deficit_facts`; status degrades to `in_plan` vs a generic
"the plan deploys by your goals; category picks below" framing.

### Category with no ranked funds

Named category not in the ranking ("REIT funds") → honest: "we don't have
ranked funds in that category" + steer to the goal-based plan. Never invent.

## Contracts

### Extractor (`ainv_engine/chat.py`)

- `_DeployRequest` gains `focus_category: Optional[str]` (free text; e.g.
  "smallcap", "gold"). Field description instructs: set ONLY when the customer
  is asking to invest in that category — not for passing mentions ("I sold my
  smallcap fund" → null).
- `extract_deploy_request(question, history)` — signature gains the recent
  conversation history (last ~6 turns, same compact block format the intent
  classifier uses). Prompt rules: amounts/cadence/category from history apply
  when the current message omits them; the CURRENT message always wins on
  conflict. **A historical amount qualifies for reuse ONLY when the customer
  stated it as money they want to invest/deploy — salary, income, expenses,
  goal targets, and hypothetical/what-if figures NEVER qualify; when in doubt,
  return null** (null degrades to the polite re-ask — the safe failure; a
  confidently wrong amount would silently persist a run the customer never
  asked for). `max_tokens` 150 → 200 (headroom for the extra field; measured
  output today ~57 tokens).
- The deterministic regex fallback `parse_deploy_request` stays current-message
  only (safety net, unchanged) — a fallback-path category is simply not
  detected; the flow degrades to today's behavior.
- **Normalization is deterministic and post-hoc**: the LLM's free-text category
  is matched against the actual `sub_category` values in `get_fund_ranking()`
  via a synonym map (`smallcap/small cap → "Small Cap Fund"`, `gold → "Gold
  ETF"`, `tax saving/elss → "ELSS"`, `flexicap → "Flexi Cap Fund"`,
  `international/us/overseas → "FoF Overseas"`, …) + case-insensitive direct
  match. No match → treated as no category for routing, but the asked TEXT is
  kept so the reply can honestly say "we don't rank funds there".

### Category helper (new: `ainv_engine/category.py`)

- `resolve_category(text) -> str | None` — the normalization above (pure;
  reads `get_fund_ranking()`).
- `top_funds_for_category(category, n=3) -> list[RankRow]` — filter ranking
  rows by `sub_category`, order by rank. Also exposes the category's
  `asset_subgroup`(s) for the status computation.
- `category_status(category, deficit_facts, buys, exclude_subgroups) -> str`
  — pure function returning one of the four states, the SIP degraded form, or
  `not_ranked` (canonical category is None) — the fifth state that unifies the
  unknown-category edge into the same reply path. **Precedence (first match
  wins):** `not_ranked` → `excluded_by_policy` → `in_plan` →
  (gap-gated) `at_or_above_ideal` / `subgroup_funded_other_funds`. If a
  category's funds span multiple subgroups, use the subgroup of its top-ranked
  fund. **Gap-gating (final review 2026-07-04):** `at_or_above_ideal` requires
  the subgroup's gap to actually be ≤ 0 (row absent or `gap_inr <= 0`) — a
  positive gap with `buy_inr == 0` (sub-₹100 rounding, caps, or fund scarcity)
  degrades to `plan_by_goals`, the generic narration that is never false;
  claiming "at/above ideal" there would invert the advice for an underweight
  customer.

### Chat handler (`handle()`)

```
amount, cadence, raw_category = await extract_deploy_request(question, history)
category = resolve_category(raw_category)   # canonical sub_category | None
if raw_category is None:          # cases 3 & 4 — bit-for-bit today's flow
    ... unchanged ...
elif amount is None:              # case 2 — category probe
    reply = format(category_ask block + ask amount)          [NEW body prompt]
else:                             # case 1 — plan + category overlay
    outcome = compute_additional_investment_result(...)      [UNCHANGED call]
    reply = format(plan facts + category_ask block)          [extended bodies]
```

The extractor returns the RAW category text; the handler canonicalises it.
An unrecognised category (`resolve_category` → None with raw text present)
still routes through the category paths with `status="not_ranked"` and empty
`top_funds` — one code path, honest reply everywhere.

### Facts pack / prompts

- `build_ainv_facts_pack(..., category_ask=None)` — optional block:
  `{asked_text, category, status, top_funds: [{fund, sub_category}],
  subgroup_ideal_inr, subgroup_current_inr}` (subgroup numbers from
  `deficit_facts` when available).
- Deficit + SIP body prompts gain a conditional `category_ask` section: name
  the top funds, deliver the status story per state, ALWAYS the caveat
  ("concentrating in one category is not what we'd recommend — the plan spreads
  by your goals"). Ratio/allocation guardrails from the deficit body apply.
- New `_AINV_CATEGORY_PROBE_BODY` for case 2: acknowledge the category, name
  the top funds + caveat, ask amount & cadence. Deterministic fallback brief
  (fund names + ask) mirrors `_build_fallback_ainv_brief`'s style.
- **Case-1 fallback must not drop the category ask**: when the formatter fails
  on a category+amount turn, `_build_fallback_ainv_brief` appends a
  deterministic category line ("On smallcap specifically: our top-ranked picks
  are X, Y, Z — but we recommend the plan above") so the rare fallback path
  never regresses to ignoring the customer's question (the original bug).
- `action_mode="category_probe"` for case 2's telemetry (case 1 stays
  `compute`).

### Persistence

No schema change. When a category ask rides a persisted run, add
`"focus_category": <canonical>` to the existing `request_extras` merge
(analytics can count category asks per run).

## Edge cases

| Case | Behavior |
| --- | --- |
| "smallcap only" follow-up after "invest 5L" | history supplies 5L + lumpsum → full case-1 reply (the original transcript, fixed end-to-end) |
| Amount in history AND current message | current wins ("actually make it 2L" → 2L) |
| Passing mention ("I sold my smallcap fund, invest 5L") | extractor rule: no category → plain plan |
| Unknown category ("REIT") | honest "we don't rank funds there" + goal-based steer |
| ELSS / excluded subgroups | `excluded_by_policy` state — funds named, policy stated, plan never buys there |
| Extractor LLM failure | regex fallback (no category) → today's behavior; never an error |
| Ambiguous history figure (salary, goal target, what-if) | NOT reused — extractor returns null amount → polite re-ask; wrong-amount persistence is the failure we refuse |
| SIP + category | legacy plan + category overlay with degraded status |
| Multiple categories in one ask ("smallcap and gold") | extractor returns the DOMINANT one (prompt rule); v1 explicitly single-category |

## Testing

- **Extractor**: history reuse (amount + cadence + category — incl. the
  two-turn probe → "5L" conversation where the category comes from history),
  current-message override, passing-mention null, category+amount both present,
  salary/income figure in history NOT reused, invest-amount beats salary figure
  when both present, LLM-failure regex fallback.
- **category.py (pure)**: synonym resolution incl. unknowns; top-N ordering;
  all four `category_status` states + SIP degraded form.
- **Handler routing**: category+amount → compute called + `category_ask` in
  facts; category-only → probe body, compute NOT called; no-category paths
  byte-identical to today (regression).
- **Facts/prompts**: `category_ask` present/absent; probe body exists; caveat
  text asserted in both bodies.
- **Live smoke** (post-implementation): replay the original 4-turn transcript
  verbatim — every turn must now resolve without re-asking or hallucinating.

## Out of scope

- Multi-category asks (v1 = dominant category only).
- Category-only deployment (rejected 2026-07-02 — we never build a
  concentrated plan).
- Fund-level asks ("should I buy Nippon Small Cap?") — portfolio_query /
  rebalancing territory, not this flow.
- Durable category preferences on the profile (future; relates to the
  conversational profile-fill direction).
