# Save a Rebalancing Plan — Design Spec

**Date:** 2026-08-27
**Status:** Approved (design); implementation not started. **Three staff-engineer audits folded in 2026-08-27** (see "Audit corrections" + the v2 section).
**The feature (v1):** a customer can **mark a (plain) rebalancing they computed in chat as their committed plan** — it's flagged, and the portfolio page shows that saved plan. A trivial change on top of what already persists.
**Explicitly deferred to v2:** saving a **tilted** rebalancing, persisting the tilt as a re-applicable preference, surviving a CAMS upload, and any execution/SIP gating. **In v1 a new CAMS upload wipes the saved plan** (it lives in `rebalancing_runs`, which the reset deletes) — the customer re-saves. Accepted v1 limitation.
**Scope (v1):** one nullable `origin` column on `rebalancing_runs`, a `POST /rebalancing/{run_id}/save` endpoint (a status flip), and a `GET /rebalancing/current` read. No capture surface, no new table, no ingest hook, no change to execution or SIP.

## Audit corrections (folded in 2026-08-27)

- **Tilt-save is deferred to v2.** Plain rebalances *already* persist (`chat.py:719, 832`, `persist=True`) and already return their run id to the client (`ChatHandlerResult.rebalancing_recommendation_id`, `chat.py:743, 858`), so plain-save is a **one-line `origin` flip by run_id** — no capture. The entire capture surface (candidate runs / a session stash) exists *only* because tilt turns return no run id; it moves to v2 with the rest of the tilt work.
- **v1 does NOT touch execution or SIP.** An earlier draft claimed execution "stays behind `status='approved'`" — **that gate does not exist**: `execute_rebalance_buys` fires on the latest run unconditionally (`fp_service.py:913-943`), the customer's explicit "execute" action being the only gate. v1 leaves that exactly as-is, so "Save does not trigger orders" holds **because v1 changes nothing about execution** — no firewall, no origin-gating (those, and their regressions for non-savers, are a v2 concern that arrives with tilt-save).
- **The saved run is chosen by `origin='saved'` first, then `created_at`.** `save_plan` demotes any prior saved run (`origin=NULL`), so exactly one run is ever saved; the read orders saved-first via a `case(...)` and tie-breaks the no-saved fallback by **immutable `created_at`** — NOT `updated_at`, which `PUT /{run_id}/status` and the demote itself bump (using `updated_at` could rank an old, touched run above a newer compute). *(This corrects an earlier draft that keyed on `updated_at`; the demote + `case(...)` make that moot.)*
- **`GET /current` registered before `GET /{run_id}`** or the literal path parses as a run UUID and 422s (the router already does this for `/readiness`, `rebalancing_router.py:74-76`).

## Problem

Plain rebalances persist and show on the page, but there is **no concept of a committed plan** — the page just reflects the latest computed run, and the customer can't mark one as *theirs*. v1 introduces that concept for plain rebalances as the foundation the tilt/preference/CAMS-survival work (v2) builds on. (The headline motivation — a *tilted* plan vanishing because tilt turns are `persist=False` — is v2.)

## The feature — "Save this rebalancing" (v1, plain only)

On a plain rebalancing recommendation in chat, a **"Save this rebalancing"** control. It marks that run as the customer's committed plan; the portfolio page then shows the saved plan.

### The save flow — a REST flip (NOT a chat turn)
The button calls `POST /rebalancing/{run_id}/save` with the run id the chat already returned (`ChatHandlerResult.rebalancing_recommendation_id`) — it does **not** go through the classifier/formatter. The endpoint:
1. Sets `origin='saved'` on that run and **demotes any prior saved run** for the user (`origin=NULL`), so exactly one run is ever committed.
2. **Owns `await db.commit()`** (mirroring the existing REST mutation `update_status`, `rebalancing_router.py:215`).
3. Is **idempotent** — saving the same run twice is a no-op (it is excluded from the demote and re-set to `saved`).

## What powers the page

`GET /rebalancing/current` — the customer's **committed run** (`origin='saved'`; exactly one), else the latest run by `created_at`, else the empty/prompt state. Register it **before** `GET /{run_id}`. Rendering is the existing `RebalancingRunDetailResponse` (no renderer change).

## Provenance: the `origin` column
A nullable `origin` on `rebalancing_runs`, set to `'saved'` by the save endpoint; NULL for everything else in v1. **Not `knob_snapshot`** (it holds the real tax-knob snapshot read back for tax facts, `service.py:514-530`). v2 will populate additional values (`chat`/`compute`/`candidate`) once the firewall exists.

## Execution & SIP — unchanged in v1
No change. `execute_rebalance_buys` and the SIP mirror `latest_buy_trades_by_subgroup` keep using "latest run" exactly as today; saving does not arm, gate, or alter them. (Gating them to the saved plan — and the non-saver regression that creates — is v2, arriving with tilt-save.)

## Edge cases
- **CAMS re-upload** → `reset_user_financial_data` wipes `rebalancing_runs`, so **the saved plan is gone** — accepted in v1; the page returns to the latest-run/empty state and the customer re-saves. (v2 removes this via the tilt-preference + regenerate.)
- **Save a run that's no longer the latest** (customer computed a newer one after) → `/current` orders `origin='saved'` first, so it still returns the saved one; the newer unsaved run does not displace it.
- **Save fails** → logged; nothing changed; retry.

## Testing
- **Save** → the target run gets `origin='saved'`; `GET /rebalancing/current` returns it; a second click is a no-op (no duplicate, no error).
- **Read precedence** → with a saved run present, `/current` returns it even when a newer unsaved run exists; with none saved, `/current` returns the latest run; with no runs, the empty state.
- **Route ordering** → `GET /rebalancing/current` resolves (not a 422 from `/{run_id}`).
- **Execution untouched** → `execute_rebalance_buys` behavior is identical with and without a saved run (no regression).
- **CAMS wipes it** → after a CAS upload, `/current` no longer returns the saved plan (the accepted v1 limitation).

## v2 (deferred — the real weight of the feature)

Saving a **tilted** plan and making it durable. Captured here so v1 forecloses none of it and the prior audits aren't re-discovered:

- **Tilt-save needs a capture surface.** Tilt turns are `persist=False` and return no run id, and the overrides never reach the client (built and discarded in `_handle_preference_counterfactual`, `chat.py:998-1078`) — so Save cannot recompute from echoed conditions. Options: persist the tilt run at generation as a **candidate** run (origin `candidate`, flipped to `saved`), or **stash** its serialized result in a new JSONB column (the `chat_session_state.awaiting_save` scaffold is dead and holds no payload — a real column/migration is required). The candidate approach leaks a "latest run" into **three** reads (SIP, execution, `list_runs`) that must all be gated.
- **Tilt-as-preference:** a nullable JSONB `saved_rebalancing_tilt` (the full resolved `TiltResult.mix_pct` + `pure_equity_only`, not the raw ask) on **`investment_profiles`** (1:1 per user, survives the CAMS reset, already eager-loaded — a *setting*, not a rebalancing table).
- **CAMS survival:** a **post-commit `BackgroundTask` in its own `AsyncSession`** at the end of CAMS ingest regenerates the plan with the saved tilt (own session for isolation — a same-session compute failure would roll back the upload's own holdings writes; run after the NAV backfill or the input builder can't price; readiness gate; `load_user_for_ai` for the full user graph).
- **Execution/SIP firewall + taxonomy:** the `origin` set gains `chat`/`compute`/`candidate`; the firewall must gate SIP + execution + `list_runs`, and must decide the non-saver fallback (gating to `saved`-only regresses users who never save); the Invest-page compute (`ai_engine/routers/rebalancing_router.py:57-62`, `chat_session_id=None`, `persist=True`) needs its own origin value. A captured tilt run's `source_allocation_run_id` points at the *un-tilted* AA run — harmless for rendering but not a "matches this target" link.

## Not covered / future (beyond v2)
Standing preferences beyond one saved plan (named plans, per-goal tilts); superseded-plan history UI; on-platform execution of a saved plan (the transaction layer — deliberately future).
