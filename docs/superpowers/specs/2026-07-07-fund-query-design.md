# fund_query — Grounded answers about a specific mutual fund

**Status:** Design approved & audited (2026-07-07) · pending final spec review
**Workstream:** WS1 of the Pi chat-flow remediation (follows F4, F5 shipped)

> Revised after a 9-point design audit (arch / complexity / completeness /
> over-engineering). Decisions recorded inline; see the audit log at the end.

## Problem

Production incident (Sourabh Agarwala, 2026-07-03/04): a fund-detail question —
*"historical returns of Parag Parikh Flexi Cap vs peers"* — was misrouted to the
ungrounded `general_market_query` flow, which **fabricated** returns
(`−3.25% / 15.32% / 15.10%`, "ranks 3 of 24", "per our live data") that exist in
**no data source**. Two root causes:

- **F2 — no intent owns fund-specific questions.** The 8-intent taxonomy is keyed
  on the *action* the customer wants; "explain/compare a specific fund" matches
  none, so the classifier picks the nearest neighbour (`general_market_query`).
- **F1 — that flow has no fund data and is forbidden to name funds.** The model
  filled the vacuum with plausible fabrication, wrapped in a "live data"
  attribution.

This spec adds a dedicated, **grounded** capability so fund questions are answered
only from real data (our ranking CSV + stored NAV history), never invented.

## Goal

A customer can ask about a specific mutual fund — why we recommend it, its
historical returns, how it compares to peers — and get an answer grounded in real
data, for any fund we can identify with certainty, with honest handling of funds
we have no house view on and of missing data.

## Non-goals (v1)

- **Agentic tool-calling** (audit A1). fund_query is a **two-step single-shot**
  flow, matching every existing engine. Agentic orchestration is built first-class
  *if/when* a genuinely dynamic-orchestration feature (open research, agentic
  advisor) needs it — not seeded speculatively here.
- **Structured "recommended-funds" context** (classifier injection / `TurnContext`
  threading / an additional-investment read-service). v1 resolves funds from the
  **named fund + conversation history**, validated against Sourabh's transcript.
  Added later, **symmetrically** for rebalancing *and* additional-investment, only
  if pronoun-resolution failures are observed.
- **Heavy disambiguation subsystem** (audit C1). Only a lightweight confidence gate
  + a clarifying question when the name is ambiguous.
- **mfapi (external) calls inside the flow** (audit B3). NAV comes from the DB only.
- **Category-percentile rankings** ("3 of 24"). We hold a *house shortlist rank*,
  not a performance percentile — never present one.
- **LangGraph** / any rebalancing-engine change (that is WS4).

## Architecture — two-step single-shot (audit A1)

The subject is variable (which fund?), but the control flow is **fixed**
(`resolve → fetch → narrate`), so we don't need the LLM to orchestrate — we use the
established single-shot idiom twice, with deterministic Python in between.

```
classifier → fund_query intent
  → flow_fund_query                       (app/domains/ai_engine/services/flow.py)
    → fund_query_service.answer_fund_query(question, ctx)   [mutual_funds domain]

        1. EXTRACT  (LLM, single-shot structured):
             question + conversation history
               → { fund_name(s), asked_for: reasoning | returns | comparison }

        2. RESOLVE + BUILD  (Python, deterministic — no LLM):
             resolve name → canonical Direct-Growth scheme_code   (see D1)
             build a small FundFacts pack (reasoning + CAGR + peers)
             ambiguous match → return a clarifying question, skip narrate

        3. NARRATE  (LLM, single-shot forced-tool):
             FundFacts pack → customer answer, under the guardrail
```

Both LLM passes are plain single-shot structured outputs (trivial to test/mock);
there is **no agentic loop**. The `AI_Agents/src/fund_query/` engine is
**DB-agnostic** — every ORM/DB read (resolution, NAV, CSV) lives in the app-layer
`mutual_funds` service, per the house AI-module split.

### Fund resolution (audit D1)

- **Universe:** any fund in `mf_fund_metadata`.
- **Canonicalize to Direct-Growth.** A fund name maps to ~6 AMFI schemes
  (Regular/Direct × Growth/IDCW), each with its own NAV. For a general
  "tell me about fund X", resolve to the **Direct-Growth** scheme — the canonical
  representative of a fund's track record, and what our shortlist already
  recommends. This collapses the variant explosion to one `scheme_code`.
  *(Implementation seam to confirm: identify Direct-Growth via the scheme-name
  pattern or a plan/option field on `mf_fund_metadata`.)*
- **Held funds use the customer's actual scheme.** If the question is about a fund
  the customer *holds*, use their real held `scheme_code` (their actual plan — a
  Regular-plan holding's returns differ from Direct), not the Direct-Growth
  canonical.
- **Confidence gate (the only disambiguation).** One clearly-best fund match →
  proceed. Genuinely ambiguous (vague/partial name, similar fund names) → return a
  **clarifying question** ("did you mean *X*?"), never a guess. No candidate-list
  subsystem.

### FundFacts pack (Python-built, deterministic; only real data or explicit nulls)

- **reasoning** — CSV `selection_reason` + shortlist `rank` + `asset_subgroup`, if
  the fund's ISIN is in the ranking CSV; else `null` (no house view).
- **returns** — 1y/3y/5y **CAGR** from stored NAV (see A2/B3). A horizon with
  insufficient stored history → `null` + honest note.
- **peers** (only when a comparison is asked) — funds of the **same `sub_category`**
  from our shortlist, self-excluded (audit B2). Like-for-like (Flexi Cap vs Flexi
  Cap), never the broader subgroup. None → "no direct peers in our recommended set".

### Returns computation (audit A2 + B3)

- **CAGR math → a new pure primitive in `AI_Agents/src/financial_primitives/`**
  (e.g. `returns.py`): `cagr(start_value, end_value, years)`. The genuine reuse
  point for annualized returns anywhere.
- **Scheme CAGR → a reusable `mutual_funds`-domain helper**: reads NAV from
  `mf_nav_history` for a `scheme_code` (**DB only — no mfapi call**), computes
  1y/3y/5y via the primitive. History completeness is the scheduler's job,
  out of band; the flow never blocks on an external fetch.
- **`holding_detail_service`'s point-to-point computation is left untouched** —
  it's a different metric for a different surface; no speculative merge.

### Guardrail (audit B1) — prompt-only, honestly scoped

The engine prompt enforces:
- State **only** numbers present in the FundFacts pack; never recompute, round, or
  invent.
- Frame rank as **"our shortlist"**, never a category percentile.
- No house view → returns + "not one we actively recommend"; missing returns →
  say so. Never fabricate rationale or figures.

Grounding comes from **real-data-in-context + this strict rule**, which removes the
incident's root cause (there, the flow had *no data at all*). We make **no
schema-level hard guarantee** — the answer text is free-form prose. A post-hoc
numeric-trace validator (verify every number in the answer traces to the pack) is a
**documented future hardening**, not v1.

## Data flow — *"what are Parag Parikh's returns vs peers?"*

1. Classifier → `fund_query`.
2. `flow_fund_query` → `fund_query_service.answer_fund_query`.
3. **Extract:** `{ fund: "Parag Parikh Flexi Cap", asked_for: comparison }`.
4. **Resolve + build:** name → Direct-Growth `scheme_code`; pack =
   CSV `selection_reason`/rank + 1/3/5y CAGR (DB NAV) + same-`sub_category`
   shortlist peers' CAGR.
5. **Narrate:** model narrates the pack; guardrail enforced; structured answer.

## Components (new)

- `AI_Agents/src/fund_query/` — `models.py` (`ExtractResult`, `FundFacts`,
  `FundQueryResponse`), `orchestrator.py` (two-pass), `llm_client.py` (**copy**),
  `skill_executor.py` (**copy**), `extract.md`, `fund_query.md` (narrate),
  `guardrails.md`, `__init__.py`. *(Audit C2: copy only the two generic helpers;
  author the rest fresh — no fork-and-bend of portfolio_query's orchestrator.)*
- `AI_Agents/src/financial_primitives/returns.py` — CAGR primitive.
- `app/domains/mutual_funds/services/`:
  - `fund_query_service.py` — gateway: extract call → resolve → build pack →
    narrate call → string.
  - a **fund resolver** (name → Direct-Growth `scheme_code` + confidence gate;
    held-fund path).
  - a **scheme CAGR helper** (NAV → 1/3/5y CAGR, DB-only).
  - a thin **CSV lookup** wrapper (by-ISIN / by-`sub_category`) over
    `get_fund_ranking()`.
- `flow_fund_query` + FLOWS row in `app/domains/ai_engine/services/flow.py`.
- Intent registration (5 points below).
- **Logic doc** `AI_Agents/Reference_docs/Logics_reference_docs/fund_query.md`
  (audit B4), version-bumped — grounds the customer-facing answers.

## Intent registration (5 points)

1. Enum member — `AI_Agents/src/intent_classifier/models.py` (`Intent`).
2. `_IntentLiteral` — `classifier.py` (kept in sync by `test_intent_classifier_schema.py`).
3. Taxonomy prompt entry — `prompts.py`: definition + triggers + examples + **Key
   distinctions** vs `portfolio_query` (a fund I *hold* vs one asked about),
   `additional_investment` ("which fund should I buy" vs "tell me about this fund"),
   `general_market_query` (macro vs a specific fund); plus follow-up guidance.
4. FLOWS row — `flow.py`: `"fund_query": flow_fund_query`.
5. Display-label map — `intent_classifier_engine.py` `_INTENT_LABELS`.

## Error handling

| Case | Behaviour |
|---|---|
| Ambiguous name | clarifying question ("did you mean X?") — no candidate subsystem |
| Fund not in DB | "we can't find that fund" |
| Short/no stored history | that horizon `null` + honest note; never fabricate |
| Not in shortlist | returns + "not one we actively recommend" |
| Engine/auth exception | canned reply (mirror `portfolio_query` exception mapping) |

## Testing

- **CAGR primitive** — pure unit tests (known start/end/years; edge: <1y, degenerate).
- **Scheme CAGR helper** — mock `mf_nav_history` rows → 1/3/5y CAGR; short history → null.
- **Resolver** — Direct-Growth canonicalization; held-fund uses held scheme;
  ambiguous → clarify.
- **CSV lookup** — by-ISIN/name hit + miss; `sub_category` peers, self-excluded, empty.
- **Extract pass** — representative question → structured `{fund, asked_for}`.
- **Narrate / guardrail** — pack → answer; no non-pack numbers; house-rank framing.
- **Classifier** — routes to `fund_query`; Key-distinction cases vs each neighbour;
  drift test stays green; add cases to the prompt eval gate
  (`scripts/run_prompt_eval_gate.sh`) **before merge**.

## Risks / rollout

- New intent next to `general_market_query`/`portfolio_query` — sharpen the boundary
  in the prompt; run the eval gate before merge.
- Two LLM passes per fund question (extract → narrate) — accepted.
- "Any DB fund" is safe because Direct-Growth removes variant ambiguity and the
  confidence gate catches fund-identity ambiguity.
- DB-only returns depend on stored history depth; thin history → fewer horizons
  shown (honest), addressed out-of-band by the scheduler, never by an inline fetch.

## Build order

1. CAGR primitive (`financial_primitives`) + tests.
2. Scheme CAGR helper (`mutual_funds`, DB-only) + tests.
3. CSV by-ISIN / by-`sub_category` lookup wrapper + tests.
4. Fund resolver (Direct-Growth canonicalization + confidence gate + held-fund) + tests.
5. FundFacts builder (assemble reasoning + returns + peers) + tests.
6. `AI_Agents/src/fund_query/` engine (extract + narrate passes) + prompts/guardrails.
7. `fund_query_service` gateway + `flow_fund_query`.
8. Intent registration + classifier prompt + eval-gate cases.
9. Logic doc (`Logics_reference_docs/fund_query.md`), version-bumped.
10. End-to-end verify against the incident transcript.

## Audit log (2026-07-07)

| # | Finding | Decision |
|---|---|---|
| B1 | Guardrail claim overstated | Prompt-only; correct the claim; validator = future hardening |
| A1 | Tool-calling is a new pattern | Two-step single-shot instead |
| D1 | "Any DB fund" mis-resolution risk | Any DB fund + Direct-Growth canonicalization + confidence gate/clarify |
| C1 | Disambiguation subsystem | Lightweight clarify only (folded into D1) |
| B2 | Peers by subgroup not like-for-like | Peers = same `sub_category`, from shortlist, self-excluded |
| B3 | On-demand mfapi backfill latency | DB-only NAV; null horizons we can't cover; no inline fetch |
| C2 | Full 6-file engine clone | Copy only `llm_client`/`skill_executor`; author the rest fresh |
| A2 | Returns duplication | CAGR primitive in `financial_primitives`; reusable `mutual_funds` helper; leave `holding_detail` |
| B4 | Logic docs missing | Ship `fund_query.md` Logic doc, version-bumped |
