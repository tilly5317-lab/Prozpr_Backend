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

- **Grounding:** every number in the reply comes from the rebalancing engine. The formatter is forbidden to produce or alter trades. (Recompute — never a narration-layer transform.)
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
| Within-subgroup distribution | `AI_Agents/src/additional_investment/selection.py:87-187` `select_funds_sip` (equal-split → capped rank-walk) | reuse the **within-subgroup** mechanics only; cross-subgroup reallocation is **new** code |
| Fund ranking | `rebal_engine/fund_rank.py:75` `get_fund_ranking` | fund/subgroup ordering for the reshape |
| Engine | `AI_Agents/src/Rebalancing/pipeline.py:90` `run_rebalancing` | run **once, unmodified**; buy reshape happens after, not inside |
| Facts pack | `service.py:209` `build_rebal_facts_pack` | narration input for constrained result |
| Session state | `chat/models/chat_session_state.py:29`; dormant `awaiting_save` `:37` (dead code) | add `pending_clarification` field |

## 5. Architecture

### 5.1 Constraint model

Two constraint fields, extracted from the follow-up turn:

- `target_fund_count: int | None` — max number of **new-buy** funds. (v1 = new buys, **not** "portfolio to N funds total" — see §7.)
- `allowed_subgroups: list[str] | None` — redeploy the **entire** buy budget into these subgroups only (the "only largecap" / "only midcap+smallcap" ask). Every recommended buy fund must sit in one of them; freed cash is redirected, **not** left idle. Sells stay as the engine computed them (interpretation (a); whole-portfolio retarget = (b) is deferred, §7). Canonicalized to subgroup keys.

Both default to `None` (no constraint). They live on `RebalanceAction` (`chat.py:49-67`) as new optional fields, extracted by the same Haiku call that already classifies the action.

### 5.2 Application — run once, then deterministically reshape the buys

**The engine runs exactly once, unmodified.** Constraints do **not** feed a modified input or trigger a second engine run — a re-run would recompute the sells from a changed target (e.g. "only largecap" would make the engine sell all non-largecap → interpretation (b), the deferred forced-tax case). Instead, the engine produces its real, tax-aware sells (**frozen**) and a real total **buy budget**; a **deterministic** step then redistributes *only that buy budget* across the funds the customer allows. Sells, total buy amount, and tax are all untouched — tax lives entirely on the sell side, which we never change.

The reshape is two layers, and only the second is reused:

1. **Cross-subgroup budget reallocation — NEW logic.** Both constraints move budget *between* subgroups, which nothing in the codebase does today:
   - `allowed_subgroups`: pool the entire buy budget and reallocate it across *only* the allowed subgroups (split by the PAA ideal weights of those subgroups, renormalized). Budget the engine had aimed at other subgroups is moved in, not left idle.
   - `target_fund_count`: rank the buy positions globally and keep the top-`N` funds; the dropped funds' amounts are merged into survivors. Reducing the *count* can drop a subgroup's only fund, so its budget shifts to another subgroup — hence cross-subgroup.
2. **Within-subgroup fund distribution — REUSED.** Once each surviving subgroup has its (possibly enlarged) budget, distribute it across that subgroup's ranked funds using the `select_funds_sip` mechanics (equal-split + capped rank-walk, `selection.py:87-187`). This layer is liftable as-is; the cross-subgroup layer above it is not.

Constraints may combine (`allowed_subgroups` **and** `target_fund_count`): filter to allowed subgroups first, then collapse to `N` funds within them.

### 5.3 Where it lives: engine run (no persist) → reshape stage → narrate

`compute_rebalancing_result` (`service.py:545`) currently computes **and** persists. The `consolidate` path needs neither the persist nor the counterfactual-override hook (that hook re-runs the engine — wrong mechanism for us). Flow:

1. Run the engine **once, normally** — reuse a compute-only variant of `compute_rebalancing_result` (a `persist=False` parameter or sibling) so **no `RebalancingRun`/child rows are written**. Returns the real `RebalancingComputeResponse` (frozen sells + buy list + buy budget).
2. **Deterministic buy-reshape stage** (new module) applies §5.2 to the buy list, producing a reshaped buy list with the same total and the same sells.
3. Build the facts pack via `build_rebal_facts_pack` from the reshaped result and narrate via the existing formatter.

The reshape stage is pure/deterministic Python over real engine numbers — grounded, not fabrication. (Fabrication risk is the *LLM* inventing trades; our own code redistributing real amounts is fine.)

### 5.4 Clarification pad — `pending_clarification`

New field on `ChatSessionState` (revives the dormant scaffold; `awaiting_save` stays dead/removed separately):

```
pending_clarification: dict | None
# e.g. {"kind": "consolidation", "asked": "target_fund_count",
#        "partial": {"allowed_subgroups": ["largecap"]}}
```

Flow:
1. `consolidate` action detected but a needed constraint is missing → **write** `pending_clarification` with what we asked + any partial constraint already parsed → return the clarifying question. (Ask **once**.)
2. Next turn: **read** `pending_clarification` first. Interpret the customer's message as the answer to `asked`, merge into `partial`.
3. If complete → recompute (§5.3), narrate, **clear** `pending_clarification`. If still incomplete → update `partial`, ask only the still-missing piece.

This replaces the stateless `clarify` return at `chat.py:397-401` for the consolidation case.

### 5.5 Same-conversation continuity

The constrained result is not persisted, so a following turn ("what's the tax on that?") must still see the consolidated plan, not the original run. v1 mechanism: cache the last constrained `RebalancingComputeResponse` in session/turn context (alongside `pending_clarification`) so the next turn's classifier/narration references it. (Exact carrier — `ChatSessionState` blob vs in-memory `last_agent_runs` overlay — is a plan-level decision; both are session-scoped, neither persists a `RebalancingRun`.)

## 6. Data flow

```
follow-up turn
  → _detect_rebal_action  (classify → consolidate; extract target_fund_count / allowed_subgroups)
  → read pending_clarification; merge answer
      ├─ incomplete → write/update pending_clarification → ask the one missing piece → END
      └─ complete   → clear pending_clarification
            → run engine ONCE, compute-only (persist=False) → frozen sells + buy budget
            → deterministic buy-reshape stage (cross-subgroup realloc + within-subgroup distribute)
            → build_rebal_facts_pack (from reshaped result)
            → formatter narrates real numbers  (NO RebalancingRun written)
            → cache result in session context for next-turn continuity
```

## 7. Scope boundaries (v1)

**In:** buy-side reshape by `target_fund_count` (new-buy count) and `allowed_subgroups` (redeploy entire buy budget into named subgroups, interpretation (a)); ask-once clarification; run-once + deterministic reshape + narrate; same-conversation continuity.

**Out (deferred, stated to the customer honestly when hit):**
- **"Portfolio to N funds *total*"** — requires selling held funds purely to hit a count (touches the sell side, tax risk). The classifier must distinguish "N *new* funds" (supported) from "N funds *total*" (not yet); when the customer clearly means total, we say that mode is coming and offer the buys-consolidation we can do. (This is exactly where Sourbach landed — the honest v1 answer is "here's your buys consolidated into 5; whole-portfolio-to-5 is coming.")
- **Whole-portfolio retarget** — "only largecap" interpretation (b), i.e. selling non-largecap holdings to concentrate the whole book. v1 keeps sells frozen and only redirects the buy budget.
- **Persistence** of the reshaped run / Invest-page reflection.
- **Durable cross-run preferences.**
- **F6** holdings source-of-truth (separate, deferred).

## 8. Error handling

- Missing/garbled constraint → clarify once via the pad; never guess a fund count.
- `target_fund_count` ≥ number of candidate funds → no-op collapse (narrate "already within N").
- `allowed_subgroups` matches no ranked funds → honest "no recommended funds in <category> to buy into," no fabricated trade.
- Engine/exception in the compute-only path → degrade to narrating the original run + a plain apology; never emit invented numbers.

## 9. Testing

- **Unit — buy-reshape, fund count:** the reshape stage reduces the buy list to `N` funds, **preserves the total buy amount** (money moved, not dropped), respects per-fund caps, honors rank order. Sells identical to the engine output. (TDD, RED first.)
- **Unit — buy-reshape, allowed subgroups:** the entire buy budget is redeployed into the allowed subgroups (split by renormalized PAA weights); no buy lands outside them; total buy preserved; sells identical to the engine output.
- **Unit — pending_clarification:** ask-once → merge → recompute → cleared; partial answers accumulate; no re-ask when the pad is set.
- **Unit — no-persist:** compute-only path writes zero `RebalancingRun` rows (assert DB unchanged).
- **Grounding:** narrated fund list/amounts equal the engine output exactly (no formatter-invented trades).
- **Regression — Sourbach fixture:** replay "reduce my trades" → "5 new funds" through the flow; assert one clarify then a grounded consolidated buy list, and that the (a)/(b)/(c) 5×-loop does not recur.

## 10. Open plan-level details (not design blockers)

- Exact carrier for §5.5 continuity (`ChatSessionState` blob vs `last_agent_runs` overlay).
- Whether the compute-only path is a `persist=False` param on `compute_rebalancing_result` or a sibling function.
- Canonical subgroup vocabulary for `allowed_subgroups` mapping from free-text category names (reuse `resolve_category` / `fund_rank` subgroups).
