"""Layer 3: scripted chat conversation through the REAL rebalancing handler.

LIVE Haiku for both the follow-up action detector and the answer formatter —
nothing is patched here except what the shared conftest already does. Real
sqlite rows, real preference candidates, real candidate rebalancing runs, and
a simulated "Save plan" pill at the end.

Run explicitly (never in CI):

    RUN_PREFERENCE_CHAT_E2E=1 .venv-mac/bin/python -m pytest \
        app/domains/rebalancing/services/rebal_engine/tests/test_preference_chat_e2e_live.py \
        -m preference_chat_e2e -v -s -p no:cacheprovider

``fixture_one_subgroup_ranking`` restricts the fund universe to
``low_beta_equities`` (large cap), so the trade list is deliberately thin;
the scripted-conversation test below checks TAGGING and PERSISTENCE —
candidate preference rows, the run→preference FK, and what the pill
activates — not the trades. ``fixture_multi_subgroup_ranking`` (beta ladder +
short_debt + gold_commodities) backs a second test that DOES assert a real
others-sleeve BUY from a gold what-if.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.preference_chat_e2e

_ENABLED = bool(os.environ.get("RUN_PREFERENCE_CHAT_E2E"))

_SESSION_ID = uuid.uuid4()


async def _dump_rows(db, user_id, label: str) -> tuple[list, list]:
    """Print every preference row + rebalancing run for the user."""
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun

    pref_rows = (
        await db.execute(
            select(SavedInvestmentPreference)
            .where(SavedInvestmentPreference.user_id == user_id)
            .order_by(SavedInvestmentPreference.created_at)
        )
    ).scalars().all()
    runs = (
        await db.execute(
            select(RebalancingRun)
            .where(RebalancingRun.user_id == user_id)
            .order_by(RebalancingRun.created_at)
        )
    ).scalars().all()

    print(f"\n--- rows after {label} ---")
    print(f"SavedInvestmentPreference rows: {len(pref_rows)}")
    for r in pref_rows:
        print(
            f"  id={r.id} is_active={r.is_active} activated_at={r.activated_at}\n"
            f"    customer_choices={r.customer_choices}\n"
            f"    requested=(eq={r.equity_requested_pct}, debt={r.debt_requested_pct},"
            f" others={r.others_requested_pct})"
            f" target=(eq={r.equity_target_pct}, debt={r.debt_target_pct},"
            f" others={r.others_target_pct})\n"
            f"    resolved_targets={r.resolved_targets}"
        )
    print(f"RebalancingRun rows: {len(runs)}")
    for run in runs:
        print(
            f"  id={run.id} origin={run.origin!r}"
            f" saved_investment_preference_id={run.saved_investment_preference_id}"
        )
    return pref_rows, runs


async def _turn(db, user, history: list[dict[str, Any]], question: str, label: str):
    """One real chat turn through dispatch_chat('rebalancing', ...)."""
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat
    from app.domains.ai_engine.turn_context import TurnContext, _load_last_agent_runs

    last_runs = await _load_last_agent_runs(db, _SESSION_ID)
    ctx = TurnContext(
        user_ctx=user,
        user_question=question,
        conversation_history=list(history),
        client_context=None,
        session_id=_SESSION_ID,
        db=db,
        effective_user_id=user.id,
        last_agent_runs=last_runs,
        active_intent="rebalancing",
    )
    result = await dispatch_chat("rebalancing", ctx)
    await db.flush()

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": result.text})

    print(f"\n=== {label}: user: {question!r} ===")
    print(f"reply (rebalancing_recommendation_id={result.rebalancing_recommendation_id}):")
    print(result.text)
    return result


@pytest.mark.skipif(not _ENABLED, reason="set RUN_PREFERENCE_CHAT_E2E=1 to run (live LLM calls)")
@pytest.mark.asyncio
async def test_scripted_conversation_writes_candidates_and_pill_save_activates(
    db_session,
    fixture_user_with_holdings,
    fixture_recent_allocation_row,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    # Side-effect import: registers the @register('rebalancing') handler.
    import app.domains.rebalancing.services.rebal_engine.chat  # noqa: F401
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.profile.services.preference_save_service import (
        activate_candidate_for_run,
    )
    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
    from app.domains.rebalancing.services.saved_plan_service import save_plan

    user, _isin = fixture_user_with_holdings
    history: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(cond: bool, msg: str) -> bool:
        if not cond:
            failures.append(msg)
        return cond

    # ── Turn 1: plain compute ───────────────────────────────────────────────
    r1 = await _turn(db_session, user, history, "rebalance my portfolio", "turn 1")
    await _dump_rows(db_session, user.id, "turn 1")

    check(r1.rebalancing_recommendation_id is not None,
          "turn 1: no rebalancing_recommendation_id")
    check(bool((r1.text or "").strip()), "turn 1: empty reply text")
    pref_rows = (await db_session.execute(
        select(SavedInvestmentPreference).where(
            SavedInvestmentPreference.user_id == user.id)
    )).scalars().all()
    check(len(pref_rows) == 0,
          f"turn 1: expected 0 preference rows, got {len(pref_rows)}")

    # ── Turn 2: "100% equity" what-if → candidate row + candidate run ───────
    r2 = await _turn(db_session, user, history, "show me 100% equity", "turn 2")
    pref_rows, runs = await _dump_rows(db_session, user.id, "turn 2")

    check(bool((r2.text or "").strip()), "turn 2: empty reply text")
    if check(len(pref_rows) == 1,
             f"turn 2: expected exactly 1 preference row, got {len(pref_rows)}"):
        row2 = pref_rows[0]
        check(row2.is_active is False, f"turn 2: is_active={row2.is_active}, expected False")
        check(row2.activated_at is None,
              f"turn 2: activated_at={row2.activated_at}, expected None")
        check(
            row2.customer_choices == {
                "asset_class": {"class": "equity", "direction": "target",
                                "target_pct": 100.0}
            },
            f"turn 2: customer_choices={row2.customer_choices}",
        )
        check(row2.equity_requested_pct == 100,
              f"turn 2: equity_requested_pct={row2.equity_requested_pct}")
    else:
        row2 = pref_rows[-1] if pref_rows else None

    turn2_run_id = r2.rebalancing_recommendation_id
    turn2_run = None
    if check(turn2_run_id is not None, "turn 2: no rebalancing_recommendation_id"):
        turn2_run = await db_session.get(RebalancingRun, turn2_run_id)
        if check(turn2_run is not None, "turn 2: run row not found"):
            check(turn2_run.origin == "candidate",
                  f"turn 2: run origin={turn2_run.origin!r}, expected 'candidate'")
            if row2 is not None:
                check(turn2_run.saved_investment_preference_id == row2.id,
                      "turn 2: run FK does not point at the candidate row "
                      f"({turn2_run.saved_investment_preference_id} != {row2.id})")

    # ── Turn 3: compose gold over the live candidate ────────────────────────
    r3 = await _turn(db_session, user, history, "add a bit of gold to that", "turn 3")
    pref_rows, runs = await _dump_rows(db_session, user.id, "turn 3")

    check(bool((r3.text or "").strip()), "turn 3: empty reply text")
    row3 = None
    if check(len(pref_rows) == 2,
             f"turn 3: expected 2 preference rows, got {len(pref_rows)}"):
        row3 = pref_rows[-1]
        choices = row3.customer_choices or {}
        check((choices.get("asset_class") or {}).get("class") == "others",
              f"turn 3: customer_choices={choices}, expected asset_class class 'others'")
        check(row3.is_active is False and row3.activated_at is None,
              f"turn 3: not a live candidate (is_active={row3.is_active}, "
              f"activated_at={row3.activated_at})")

    # ── Turn 4: narrate — no new preference row ─────────────────────────────
    before = len(pref_rows)
    r4 = await _turn(db_session, user, history, "why is it selling my fund?", "turn 4")
    pref_rows, runs = await _dump_rows(db_session, user.id, "turn 4")

    check(bool((r4.text or "").strip()), "turn 4: empty reply text")
    check(len(pref_rows) == before,
          f"turn 4: preference rows changed {before} -> {len(pref_rows)}")

    # ── Turn 5: simulate the "Save plan" pill on the turn-2 candidate ───────
    print("\n=== turn 5: pill save on the turn-2 candidate run ===")
    saved_run = await save_plan(db_session, user_id=user.id, run_id=turn2_run_id)
    await db_session.commit()
    print(f"save_plan -> run={getattr(saved_run, 'id', None)} "
          f"origin={getattr(saved_run, 'origin', None)!r} "
          f"pref_fk={getattr(saved_run, 'saved_investment_preference_id', None)}")
    check(saved_run is not None, "pill: save_plan returned None")

    activated = await activate_candidate_for_run(
        db_session, user, getattr(saved_run, "saved_investment_preference_id", None)
    )
    print(f"activate_candidate_for_run -> {activated}")
    check(activated is True, f"pill: activate_candidate_for_run returned {activated}")

    pref_rows, runs = await _dump_rows(db_session, user.id, "pill save")
    active = [r for r in pref_rows if r.is_active]
    if check(len(active) == 1, f"pill: expected exactly 1 active row, got {len(active)}"):
        check(active[0].activated_at is not None, "pill: active row has no activated_at")
        if row2 is not None:
            check(active[0].id == row2.id,
                  f"pill: active row {active[0].id} is not the turn-2 row {row2.id}")
    if row3 is not None:
        row3_now = await db_session.get(SavedInvestmentPreference, row3.id)
        if row3_now is not None:
            await db_session.refresh(row3_now)
        check(row3_now is not None and not row3_now.is_active
              and row3_now.activated_at is None,
              "pill: the turn-3 row is no longer a live candidate")

    print(f"\n=== FAILURES ({len(failures)}) ===")
    for f in failures:
        print(f"  - {f}")
    assert not failures, "\n".join(failures)


@pytest.mark.skipif(not _ENABLED, reason="set RUN_PREFERENCE_CHAT_E2E=1 to run (live LLM calls)")
@pytest.mark.asyncio
async def test_gold_what_if_produces_real_others_sleeve_buy(
    db_session,
    fixture_user_with_holdings,
    fixture_buy_txn_factory,
    fixture_recent_allocation_row,
    fixture_seed_multi_subgroup_navs,
    fixture_multi_subgroup_ranking,
):
    """A gold what-if over a multi-subgroup fund universe must produce a REAL
    BUY toward gold_commodities — not just a tagged candidate row.

    The scripted-conversation test above uses ``fixture_one_subgroup_ranking``
    (large cap only), so a gold ask there can never place a trade: there is no
    gold fund in the universe to buy. This test swaps in
    ``fixture_multi_subgroup_ranking`` (beta ladder + short_debt +
    gold_commodities) so "put about 10% into gold" has a real fund to land on.
    """
    # Side-effect import: registers the @register('rebalancing') handler.
    import app.domains.rebalancing.services.rebal_engine.chat  # noqa: F401
    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
    from app.domains.rebalancing.models.rebalancing_trade import (
        RebalancingTrade,
        TradeAction,
    )

    user, isin = fixture_user_with_holdings
    # fixture_user_with_holdings seeds a ~₹600 position — below the engine's
    # ₹100 rounding step (Rebalancing/pipeline.py's rounding_step), so a
    # ~10%-of-portfolio gold target can floor to zero on pure rounding alone.
    # Top the same holding up well past that floor so a real gold buy, if the
    # engine computes one, is unambiguous rather than a rounding coin-flip.
    await fixture_buy_txn_factory(
        user=user, scheme_code=f"SCH_{isin}", units=Decimal("10000"),
        nav=Decimal("100"), txn_date=date(2024, 1, 1),
    )

    history: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(cond: bool, msg: str) -> bool:
        if not cond:
            failures.append(msg)
        return cond

    # ── Turn 1: plain compute ───────────────────────────────────────────────
    r1 = await _turn(db_session, user, history, "rebalance my portfolio", "turn 1")
    await _dump_rows(db_session, user.id, "turn 1")

    check(r1.rebalancing_recommendation_id is not None,
          "turn 1: no rebalancing_recommendation_id")
    check(bool((r1.text or "").strip()), "turn 1: empty reply text")

    # ── Turn 2: explicit gold what-if → candidate row + candidate run ───────
    r2 = await _turn(db_session, user, history, "put about 10% into gold", "turn 2")
    pref_rows, runs = await _dump_rows(db_session, user.id, "turn 2")

    check(bool((r2.text or "").strip()), "turn 2: empty reply text")
    row2 = None
    if check(len(pref_rows) == 1,
             f"turn 2: expected exactly 1 preference row, got {len(pref_rows)}"):
        row2 = pref_rows[0]
        check(row2.is_active is False, f"turn 2: is_active={row2.is_active}, expected False")
        check(row2.activated_at is None,
              f"turn 2: activated_at={row2.activated_at}, expected None")
        choices = row2.customer_choices or {}
        check((choices.get("asset_class") or {}).get("class") == "others",
              f"turn 2: customer_choices={choices}, expected asset_class class 'others'")

    gold_run_id = r2.rebalancing_recommendation_id
    if check(gold_run_id is not None, "turn 2: no rebalancing_recommendation_id"):
        gold_run = await db_session.get(RebalancingRun, gold_run_id)
        if check(gold_run is not None, "turn 2: run row not found"):
            check(gold_run.origin == "candidate",
                  f"turn 2: run origin={gold_run.origin!r}, expected 'candidate'")
            if row2 is not None:
                check(gold_run.saved_investment_preference_id == row2.id,
                      "turn 2: run FK does not point at the candidate row "
                      f"({gold_run.saved_investment_preference_id} != {row2.id})")

        # ── The required assertion: a REAL others-sleeve (gold) BUY ─────────
        gold_buys = (await db_session.execute(
            select(RebalancingTrade).where(
                RebalancingTrade.run_id == gold_run_id,
                RebalancingTrade.action == TradeAction.BUY,
                RebalancingTrade.asset_subgroup == "gold_commodities",
            )
        )).scalars().all()
        print(f"\ngold_commodities BUY trades on run {gold_run_id}: {len(gold_buys)}")
        for t in gold_buys:
            print(f"  isin={t.isin} fund={t.recommended_fund!r} amount_inr={t.amount_inr}")
        if check(len(gold_buys) > 0,
                 "turn 2: expected a non-empty gold_commodities BUY trade list, got none "
                 "(large-cap-only universe would produce exactly this failure)"):
            check(all(t.amount_inr > 0 for t in gold_buys),
                  "turn 2: gold_commodities BUY trade(s) with amount_inr <= 0: "
                  f"{[str(t.amount_inr) for t in gold_buys]}")

    print(f"\n=== FAILURES ({len(failures)}) ===")
    for f in failures:
        print(f"  - {f}")
    assert not failures, "\n".join(failures)
