# Rebalancing Constraint-Aware Consolidation (F3-B) — Design

**Date:** 2026-07-09
**Status:** Approved for planning
**Workstream:** WS4 (F6 deferred — see `memory/project_f6_holdings_source_deferred`)

## 1. The incident

Production session `4b35303c-…` (Sourbach). After we showed a 13-trade rebalancing plan, the customer asked to reduce it. Two failures followed, both visible in the transcript:

1. **Stateless re-asking.** Pi asked the *same* (a)/(b)/(c) clarifying question **five times in a row** (transcript lines 190 → 196 → 202 → 208) because nothing persisted "I am waiting for a fund count." Each turn re-classified from scratch and re-asked.
2. **Fabricated result.** When the customer finally pinned down "5 funds," Pi produced a "consolidate to exactly 5 funds" table (lines 218–232) that **no engine computed** — the LLM formatter invented it, because there is no consolidation logic in the rebalancing engine. The rebalancing chat today is **narration-only**: the engine computes one run, and every follow-up merely re-narrates it.

## 2. Goal

Let a customer's free-text constraint ("fewer funds", "consolidate my buys", "only largecap") **actually reshape the rebalancing computation** and be answered with **real engine numbers**, while asking any clarifying question **exactly once**.

## 3. Global constraints (verbatim, non-negotiable)

- **Grounding:** every number in the reply comes from the rebalancing engine or a deterministic transform of its real output. The *LLM formatter* is forbidden to produce or alter trades — narration never computes.
- **Buy-side only:** constraints reshape the **BUY** side. The tax-aware SELL logic (`step3_tax_classification` … `step5_loss_offset_top_up`) is untouched.
- **Ephemeral / chat-only:** the constrained result is narrated in chat and **not persisted** — no `RebalancingRun` row is written. The customer's canonical Invest-page plan is unchanged. (Same scope shape as SIP today; explicitly a scope call.)
- **No durable preferences:** the constraint applies to this conversation only. It does **not** carry into future rebalancing runs. No new preference store.
- **Model pin:** any LLM call uses the rebalancing Anthropic key + Haiku, matching the existing action classifier.

## 4. What already exists (reuse map)

Grounded in the current code — this feature is an *extension* of existing machinery, not new plumbing.

| Capability | Exists at | Role in F3-B |
|---|---|---|
| Intent → flow | `flow.py:133` `FLOWS["rebalancing"]`; `flow.py:48` `flow_rebalancing` | unchanged |
| Domain gateway | `rebalancing_module_service.py:18` `run(turn, ctx, prior)` | unchanged |
| Chat handler | `rebal_engine/chat.py:353` `@register("rebalancing") handle(ctx)` | add `consolidate` branch |
| Action classifier | `chat.py:614` `_detect_rebal_action`; `RebalanceAction` `chat.py:49-67` (modes: narrate/educate/counterfactual_explore/recompute/clarify/redirect) | add `consolidate` mode + constraint fields |
| Speculative pre-run | `chat.py:343` `_speculative_detect` (consumed `chat.py:390`) | unchanged (classifier extended) |
| Override allow-list pattern | `rebal_engine/overrides.py:24-32` `_REBAL_ALLOWED_OVERRIDE_KEYS`; `effective_param` `:35-43`; applied `input_builder.py:434` | pattern to mirror for constraint params |
| Orchestrator | `service.py:545` `compute_rebalancing_result`; engine call `service.py:657` `asyncio.to_thread(run_rebalancing)` | add a **compute-only, no-persist** variant |
| Input builder | `input_builder.py:210` `build_rebalancing_input_for_user`; rank-1 target `:272`; NEUTRAL held `:369-404` | **unchanged** — engine input is not constrained; reshape happens after |
| Buy amounts + ranks on the response | `RebalancingComputeResponse.rows` (`pass1_buy_amount`, `rank`, `sub_category`) | the reshape is pure pro-rata arithmetic over these — no `select_funds_sip` reuse, no ranking-CSV read; category filter matches on `sub_category` (audit corrections 2026-07-11) |
| Engine | `AI_Agents/src/Rebalancing/pipeline.py:90` `run_rebalancing` | run **once, unmodified**; buy reshape happens after, not inside |
| Facts pack | `service.py:209` `build_rebal_facts_pack` | narration input for constrained result |
| Conversation history in classifier | `build_detect_history_block` → `_detect_rebal_action` user block (`chat.py:631-638`) | carries the ask-once clarification (no session state in v1 — §5.4) |

## 5. Architecture

### 5.1 Constraint model

Two constraint fields, extracted from the follow-up turn:

- `target_fund_count: int | None` — max number of **new-buy** funds. (v1 = new buys, **not** "portfolio to N funds total" — see §7.)
- `allowed_categories: list[str] | None` — redeploy the **entire** buy budget into these categories only (the "only largecap" / "only midcap+smallcap" ask). Every recommended buy fund must sit in one of them; freed cash is redirected, **not** left idle. Sells stay as the engine computed them (interpretation (a); whole-portfolio retarget = (b) is deferred, §7). The classifier extracts the **customer's words as-is**; a **shared deterministic resolver** (`app/domains/mutual_funds/services/category_resolver.py`, vocabulary = the ranking's canonical `sub_category` values ("Large Cap Fund", "Mid Cap Fund" — matching on `sub_category`, not the coarser `asset_subgroup`: `multi_asset` alone holds ten sub_categories) via ainv's existing synonym table, MOVED to this shared home) canonicalizes them — the same single mapping `additional_investment.resolve_category` delegates to (one taxonomy source, same pattern as `scheme_classification.py`). Unresolvable word → clarify, never a silent no-match.

Both default to `None` (no constraint). They live on `RebalanceAction` (`chat.py:49-67`) as new optional fields, extracted by the same Haiku call that already classifies the action.

### 5.2 Application — run once, then deterministically reshape the buys

**The engine runs exactly once, unmodified.** Constraints do **not** feed a modified input or trigger a second engine run — a re-run would recompute the sells from a changed target (e.g. "only largecap" would make the engine sell all non-largecap → interpretation (b), the deferred forced-tax case). Instead, the engine produces its real, tax-aware sells (**frozen**) and a real total **buy budget**; a **deterministic** step then redistributes *only that buy budget* across the funds the customer allows. Sells, total buy amount, and tax are all untouched — tax lives entirely on the sell side, which we never change.

**Distribution rule — displaced-budget pro-rata (audit correction 2026-07-11).** One rule covers both constraints:

1. **Select survivors.** `allowed_categories`: survivors = the plan's buy funds inside the allowed categories. `target_fund_count`: survivors = the top-`N` buy funds (rank, then larger buy first). Combined: filter to allowed categories first, then collapse to `N` within them.
2. **Freeze survivors' own amounts.** Every surviving fund keeps the buy the engine gave it.
3. **Spread only the displaced money.** The dropped funds' combined budget is added to the survivors **pro-rata to their engine-given amounts** — e.g. plan {A ₹30k, B ₹30k, C ₹40k}, "2 funds", A dropped → A's ₹30k splits 30/70 to B and 40/70 to C. Rounding to the engine's multiple; residual onto the largest survivor.

Properties this buys (and the earlier weight-resplit draft violated): **identity** — nothing dropped → nothing moves (satisfies §8 no-op); consolidation folds small buys into big ones (what the word means to a customer); "only largecap" still redeploys the whole budget (everything outside is displaced); no dependency on PAA weights inside the reshape — pure arithmetic over the engine's own buy list. Per-fund caps are **not** re-imposed: pro-rata may push a fund past the engine's cap — accepted, the customer's constraint outranks the cap; the total buy amount is always fully placed, never idle, never outside the constraint. **Every buy representation on the response is rewritten together (logic audit 2026-07-11):** the buys live in more than one place on `RebalancingComputeResponse` — `rows[].pass1_buy_amount`, `subgroups[].actions[].pass1_buy_amount`, **`trade_list[]` (BUY entries)**, and per-subgroup `total_buy_inr`. The reshape rewrites **all** of them (BUY trade amounts updated by isin, zeroed buys dropped from `trade_list`; SELL/EXIT entries untouched) and refreshes `totals.funds_to_buy_count` — otherwise a downstream reader (the deterministic fallback brief renders from the full response, exactly on the degraded path) would narrate the old, un-consolidated buys. Invariant: after reshape, rows total == trade_list BUY total == subgroup buy totals.

**Comply-and-caution (`constraint_impact`).** Every constraint pulls the outcome away from the ideal target mix — the customer's right, but an advisory fact they must hear. The reshape stage deterministically computes a `constraint_impact` block: `target_mix_pct` (PAA ideal), `unconstrained_mix_pct` (what the normal plan achieved), `constrained_mix_pct` (what their ask achieves), `largest_deviations` vs target, the customer's `risk_profile` (already in ctx), **and `buy_mix_by_category`** — the buy budget's composition by `sub_category`, unconstrained vs constrained (logic audit 2026-07-11: the constraints act at sub_category level, but asset-class mixes can read "no change" for intra-equity asks — Sourbach's own plan stayed 18/0/82 through 13 trades. The caution must measure where the lever acts: "your new investments now go 100% into large-cap vs the plan's 19%"). The formatter picks the lens that actually moved. It rides into `build_rebal_facts_pack`; the formatter prompt is instructed to comply first, caution second — "done as you asked; note this moves you X% further from your target debt allocation" — using only numbers from the block. **v1 always complies + cautions, never refuses**: refusal thresholds are a suitability-policy project, and `constraint_impact` is already the number any future gate would read.

### 5.3 Where it lives: engine run (no persist) → reshape stage → narrate

`compute_rebalancing_result` (`service.py:545`) currently computes **and** persists. The `consolidate` path needs neither the persist nor the counterfactual-override hook (that hook re-runs the engine — wrong mechanism for us). Flow:

1. Run the engine **once, normally** — reuse a compute-only variant of `compute_rebalancing_result` (a `persist=False` parameter or sibling) so **no `RebalancingRun`/child rows are written**. Returns the real `RebalancingComputeResponse` (frozen sells + buy list + buy budget).
2. **Deterministic buy-reshape stage** (new module) applies §5.2 to the buy list, producing a reshaped buy list with the same total and the same sells.
3. Build the facts pack via `build_rebal_facts_pack` from the reshaped result and narrate via the existing formatter.

The reshape stage is pure/deterministic Python over real engine numbers — grounded, not fabrication. (Fabrication risk is the *LLM* inventing trades; our own code redistributing real amounts is fine.)

### 5.4 Clarification — history-based, ask once, **stateless** (lean decision 2026-07-10)

**No session state in v1.** An earlier draft carried a `pending_clarification` pad + sticky constraints on `ChatSessionState` (new column, migration, `TurnContext` load). Cut as over-engineering: the classifier **already reads the conversation history** (`build_detect_history_block`, threaded into `_detect_rebal_action`'s user block), and the original 5×-loop happened because the classifier had **no `consolidate` mode to express the answer with** — not because it couldn't remember. Once the mode + fields exist, the answer turn ("5 funds") resolves from history: the clarifying question we asked sits right above it.

Flow:
1. `consolidate` detected but a needed constraint is missing (e.g. "fewer funds", no count) → return the clarifying question. Nothing stored.
2. Next turn: the classifier sees our question + the customer's reply in history and emits `consolidate` with the field filled (the `_DETECT_REBAL_SYSTEM` consolidate bullet instructs: "when the assistant just asked for a fund count/category and the customer replies with one, fill it from that exchange").
3. If the customer abandons / changes topic → the classifier routes normally; nothing to clear.

**Tripwire, not insurance:** the §9 Sourbach regression eval replays the loop scenario. **Only if** it shows the loop surviving do we escalate to a durable pad (the deferred design is preserved in §7). We add state on evidence, not anticipation.

**Speculative-safety:** trivially satisfied — there is no consolidation state to write, so `_detect_rebal_action` stays a pure read and `consume_speculative_detect` is untouched.

### 5.5 Continuity — self-contained turns (lean decision 2026-07-10)

**Verification-gate result:** `last_agent_runs` is rebuilt from the DB each turn (`turn_context.py:104` → `ChatAiModuleRun`), so an in-memory overlay can't carry the consolidated view, and we don't persist it. Rather than adding sticky session state, v1 makes each consolidate turn **self-contained**:

- "make it 3 instead" → history-aware classifier emits a fresh `consolidate(3)` → re-run + reshape. No state needed.
- "what's the tax on that?" / "explain the biggest sell" → answered by the **sells, which are identical** to the canonical run. Correct either way.
- **Accepted gap:** a later *generic* follow-up ("walk me through the plan") narrates the canonical un-consolidated run; the customer restates the constraint to see the consolidated view again. Acceptable for a chat-only exploration feature; the sticky frame is deferred (§7) and can be added without rework since the reshape stage is turn-agnostic.

## 6. Data flow

```
follow-up turn
  → _detect_rebal_action  (history-aware classify → consolidate;
                           extract target_fund_count / allowed_categories;
                           "back to the full plan" routes to the existing narrate mode)
      ├─ incomplete → ask the one missing piece (stateless) → END
      │                (next turn: classifier fills the field from history)
      └─ complete   → run engine ONCE, compute-only (persist=False) → frozen sells + buy budget
            → deterministic buy-reshape (survivors keep amounts; displaced budget pro-rata)
            → build_constraint_impact (target vs unconstrained vs constrained mix)
            → build_rebal_facts_pack (reshaped result + constraint_impact)
            → formatter narrates real numbers + caution  (NO RebalancingRun written)
```

## 7. Scope boundaries (v1)

**In:** buy-side reshape by `target_fund_count` (new-buy count) and `allowed_categories` (redeploy entire buy budget into named categories, interpretation (a)); history-based ask-once clarification (stateless); run-once + deterministic reshape + comply-and-caution narration.

**Out (deferred, stated to the customer honestly when hit):**
- **"Portfolio to N funds *total*"** — requires selling held funds purely to hit a count (touches the sell side, tax risk). The classifier must distinguish "N *new* funds" (supported) from "N funds *total*" (not yet); when the customer clearly means total, we say that mode is coming and offer the buys-consolidation we can do. (This is exactly where Sourbach landed — the honest v1 answer is "here's your buys consolidated into 5; whole-portfolio-to-5 is coming.")
- **Whole-portfolio retarget** — "only largecap" interpretation (b), i.e. selling non-largecap holdings to concentrate the whole book. v1 keeps sells frozen and only redirects the buy budget.
- **Persistence** of the reshaped run / Invest-page reflection.
- **Durable cross-run preferences.**
- **F6** holdings source-of-truth (separate, deferred).
- **Engine dust-buy minimum (`min_buy_inr`)** — the root cause of "too many trades" (Sourbach's plan had ₹3,000 buys); an engine-default change with its own blast radius. Agreed as an independent sibling fix, queued separately after F3-B.
- **First-turn constraints** — a constraint embedded in the customer's *opening* rebalancing message ("rebalance me into max 5 funds") is not detected in v1; the first-turn branch computes and narrates the normal plan. Known and deliberate (deferred 2026-07-10): the follow-up path covers the observed incident shape; first-turn extraction can be layered on later since the reshape stage is turn-agnostic.
- **Session-state pad + sticky frame** (`consolidation_state` on `ChatSessionState`: pending-clarification pad, sticky applied constraints, DB column + migration + `TurnContext` load) — cut 2026-07-10 as over-engineering. Clarification rides conversation history (§5.4); continuity is self-contained turns (§5.5). **Revive only on evidence:** if the §9 Sourbach regression eval shows the clarify loop surviving the history-aware classifier, add the pad; if customers demonstrably trip on the stale-view gap, add the sticky frame. The deferred design (JSONB `{"pending": ..., "applied": ...}`, load beside `turn_context.py:80-81`, writes only in committed `handle()`) is preserved here for that day.

## 8. Error handling

- Missing/garbled constraint → ask once (stateless; history carries the answer); never guess a fund count.
- `target_fund_count` ≥ number of candidate funds → no-op collapse (narrate "already within N").
- Category word the resolver can't map ("crypto") → clarify with the categories we do invest in; never guess, never silent no-match.
- `allowed_categories` resolves but matches no buy fund in the plan → honest "no recommended funds in <category> to buy into," no fabricated trade.
- Engine/exception in the compute-only run → degrade to a plain apology; never emit invented numbers.
- Exception in the deterministic **buy-reshape stage** → fall back to narrating the engine's un-reshaped run and say we couldn't apply the constraint; never emit a partially-reshaped or invented list.

## 9. Testing

- **Unit — buy-reshape, fund count:** collapse to `N` funds; **per-fund amounts asserted**: survivors keep their engine-given buys + the displaced budget pro-rata (30/70–40/70 style); total preserved; honors rank order; **identity when `N` ≥ candidate count** (nothing moves). Sells identical to the engine output. (TDD, RED first.)
- **Unit — buy-reshape, allowed categories:** the entire buy budget lands inside the allowed categories, spread pro-rata to the surviving funds' engine-given amounts; no buy outside; total preserved; sells identical to the engine output.
- **Unit — constraint_impact:** deviation numbers (target vs unconstrained vs constrained mix) computed correctly; `buy_mix_by_category` sums to 100% on each side and shows the category shift even when asset-class mixes are flat (the intra-equity case); block present in the facts pack whenever a constraint was applied.
- **Eval rubric:** when a constraint is applied, the reply both complies and includes the caution grounded in `constraint_impact` (no invented percentages).
- **Unit — stateless clarify:** an incomplete `consolidate` (no count, no categories) returns the clarifying question and writes nothing (no session-state rows, no `RebalancingRun`).
- **Unit — no-persist:** compute-only path writes zero `RebalancingRun` rows (assert DB unchanged).
- **Grounding:** narrated fund list/amounts equal the engine output exactly (no formatter-invented trades).
- **Unit — response consistency invariant:** after reshape, every buy representation agrees — `rows` total == `trade_list` BUY total == per-subgroup buy totals == `totals.total_buy_inr`; `funds_to_buy_count` matches the non-zero buys.
- **Regression — Sourbach fixture:** replay "reduce my trades" → "5 new funds" through the flow; assert one clarify then a grounded consolidated buy list, and that the (a)/(b)/(c) 5×-loop does not recur.

**Repo-convention completion items (definition of done):**
- **Logic doc:** update + version-bump `AI_Agents/Reference_docs/Logics_reference_docs/Rebalancing*.md` with the consolidation behavior (constraints, run-once + reshape, comply-and-caution, cap-overflow rule) — engine-logic changes are incomplete without it (root `CLAUDE.md` convention).
- **Eval questions:** add consolidation cases to `AI_Agents/src/chat_eval/questions.yaml` + a focused live-run subset (mirroring `questions_mf.yaml`), including the Sourbach regression sequence; run `scripts/run_prompt_eval_gate.sh` before merging prompt changes.

## 10. Open plan-level details (not design blockers)

- Continuity is decided lean (§5.5 self-contained turns; no session state). The verification gate ran: `last_agent_runs` is DB-rebuilt each turn, which killed the in-memory overlay; the sticky-state alternative was then cut as over-engineering (§7 deferred item).
- **Live-eval finding (2026-07-11, Amoul's profile):** the **fund-count** path works end-to-end (rebalance → "reduce trades" clarify-once → "5 funds" history-fill → real reshape + grounded caution + sells frozen; no re-ask loop). The **category path** ("only invest in largecap and midcap") is **misrouted by the INTENT classifier to `asset_allocation`** (conf 0.92 — "a target-level allocation decision") before the rebalancing action detector runs, so `allowed_categories` is unreachable via that phrasing. The consolidate code is correct (unit + detector-gate green); the gap is intent-routing ambiguity. **Deferred as a v1 limitation** (2026-07-12): fixing it means nudging the intent classifier to keep "only <category>, with an active rebalancing plan" in rebalancing — its own spec + eval-gate cycle, and it risks regressing legitimate AA routing (gate protects 71/71). Gather feedback first.
- Logic-audit items **deferred by choice** (2026-07-11): (L3) survivor selection stays rank-first `(rank, -buy)` — a size-first order `(-buy, rank, isin)` was proposed for the cap-spill case (rank-2 ₹50k dropped while rank-1 ₹3k dust survives) and can be swapped later by changing one sort key + one test; (L4) no zero-buys early-out in `_consolidate` and no "already within N" formatter line — revisit if evals show awkward narration on on-target/sells-only plans.
- Whether the compute-only path is a `persist=False` param on `compute_rebalancing_result` or a sibling function.
- Category vocabulary: **decided and verified** (audit 2026-07-11) — `resolve_category` emits canonical ranking **`sub_category`** values ("Large Cap Fund", …) and the reshape matches on `row.sub_category` (not `asset_subgroup`: too coarse — `multi_asset` holds ten sub_categories, so subgroup matching would turn "only flexicap" into the whole hybrid bucket). Shared home `mutual_funds/services/category_resolver.py` = a **move** of ainv's resolver + a `resolve_categories` list wrapper; ainv re-exports, public API unchanged.
