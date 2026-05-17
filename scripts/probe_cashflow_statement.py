"""DEV-ONLY: Probe the entire cashflow_statement pipeline end-to-end.

What it does:
  1. Builds a realistic Indian-context GoalPlanningInput (50yo couple, retirement +
     child education + property goal + existing home loan + planned bonus + lumpsum spend).
  2. Runs the engine (compute_full_projection) and dumps the full GoalPlanningOutput.
  3. If ANTHROPIC_API_KEY is available, runs the agent (run_cashflow_statement_agent) with
     4 sample user queries in ONE session (so the checkpointer threads state).
  4. Dumps every intermediate to /tmp/cashflow_statement_probe/ as JSON.

Run from worktree root:
    /Users/Amoul/.../.venv-mac/bin/python scripts/probe_cashflow_statement.py

JSON outputs land in /tmp/cashflow_statement_probe/.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Wire up dotenv from the parent project's .env (the cashflow_statement module's worktree
# inherits .env via the parent backend repo).
try:
    from dotenv import load_dotenv
    # From scripts/probe_cashflow_statement.py:
    #   parents[0] = scripts/
    #   parents[1] = worktree root (goal-planning-impl)
    #   parents[2] = .claude/worktrees/
    #   parents[3] = .claude/
    #   parents[4] = Prozpr_Backend project root (where .env lives)
    candidates = [
        Path(__file__).resolve().parents[1] / ".env",  # worktree-local
        Path(__file__).resolve().parents[4] / ".env",  # parent project root
    ]
    for env_path in candidates:
        if env_path.exists():
            # override=True because the shell may have an empty ANTHROPIC_API_KEY
            load_dotenv(env_path, override=True)
            break
except Exception:
    pass

# Make AI_Agents/src importable
WORKTREE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE_ROOT / "AI_Agents" / "src"))

from cashflow_statement import (
    GoalPlanningInput, ClientProfile, RetirementInput, Assumptions,
    CustomGoal, GoalProperty, CurrentProperty, OneOffEvent, GoalType,
    GoalPlanningRequest, GoalPlanningSnapshot,            # NEW
    compute_full_projection, validate_input_only,
    run_cashflow_statement,                                     # was run_cashflow_statement_agent
    ENGINE_VERSION,
)


OUT_DIR = Path("/tmp/cashflow_statement_probe")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _build_realistic_input() -> GoalPlanningInput:
    """A representative middle-class Indian household scenario for 2026."""
    return GoalPlanningInput(
        assumptions=Assumptions(),  # all defaults: 8% income growth, 6% expense inflation, etc.
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9),
            annual_income=2_500_000,                  # ₹25L per year
            tax_rate=0.30,
            financial_assets=20_000_000,              # ₹2 Cr investable
            financial_liabilities_excl_mortgage=500_000,  # ₹5L credit card / personal loan
            monthly_household_expense=120_000,        # ₹1.2L per month
            monthly_investment_next_12m=80_000,       # ₹80K SIP
        ),
        retirement=RetirementInput(
            date_of_birth=date(1976, 5, 9),           # age 50 at update_date
            retirement_age=60,
            assumed_total_age=85,                     # 25 years post-retirement
        ),
        current_properties=[
            CurrentProperty(
                name="primary_home",
                has_mortgage=True,
                mortgage_balance=4_500_000,           # ₹45L outstanding
                mortgage_emi=42_000,                  # ₹42K/month
                mortgage_interest_annual=0.085,       # 8.5%/yr
            ),
        ],
        goal_properties=[
            GoalProperty(
                name="vacation_home",
                target_pv=15_000_000,                 # ₹1.5 Cr in today's money
                is_downpayment_only=True,
                upfront_amount=4_500_000,             # 30% downpayment
                goal_date=date(2034, 5, 9),
                mortgage_tenure_years=20,
                mortgage_interest_annual=0.085,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="daughter_college",
                goal_type=GoalType.child_local_education,
                amount_pv=2_500_000,                  # ₹25L today
                goal_date=date(2035, 6, 1),
            ),
            CustomGoal(
                name="son_abroad_education",
                goal_type=GoalType.child_abroad_education,
                amount_pv=8_000_000,                  # ₹80L today
                goal_date=date(2038, 9, 1),
            ),
            CustomGoal(
                name="daughter_wedding",
                goal_type=GoalType.child_marriage,
                amount_pv=3_000_000,                  # ₹30L today
                goal_date=date(2042, 1, 1),
            ),
        ],
        one_off_inflows=[
            OneOffEvent(description="annual_bonus_2027", amount=500_000, date=date(2027, 3, 31)),
            OneOffEvent(description="parents_inheritance", amount=2_000_000, date=date(2030, 6, 1)),
        ],
        one_off_outflows=[
            OneOffEvent(description="kitchen_renovation", amount=800_000, date=date(2028, 4, 1)),
            OneOffEvent(description="europe_trip_2032", amount=1_500_000, date=date(2032, 6, 1)),
        ],
        detail_level="full",                          # we want full γ output for inspection
    )


def _dump(name: str, obj) -> Path:
    """Write a Pydantic or plain object to JSON; return the path."""
    out_path = OUT_DIR / f"{name}.json"
    if hasattr(obj, "model_dump_json"):
        out_path.write_text(obj.model_dump_json(indent=2))
    else:
        out_path.write_text(json.dumps(obj, indent=2, default=str))
    return out_path


def _print_engine_summary(out) -> None:
    h = out.headline
    print("=" * 70)
    print(f"ENGINE OUTPUT (engine_version={out.engine_version})")
    print("=" * 70)
    print(f"  Number of goals: {h.number_of_goals}")
    print(f"  Horizon: {h.horizon_years} years (last FY: {h.last_fy_end_date})")
    print()
    print(f"  NFA today:                  ₹{h.net_financial_assets_today:>15,.0f}")
    print(f"  Sum of fund_today_pv:       ₹{h.sum_fund_today_pv:>15,.0f}")
    print(f"  Present status (NFA-needs): ₹{h.present_status:>15,.0f}  {'(ON TRACK TODAY)' if h.present_status >= 0 else '(SHORT TODAY)'}")
    print(f"  Closing NFA at horizon:     ₹{h.closing_nfa:>15,.0f}")
    print(f"  Total funded amount:        ₹{h.total_funded_amount:>15,.0f}")
    print(f"  Total shortfall (FV):       ₹{h.total_shortfall_fv:>15,.0f}")
    print(f"  Overall feasible:           {h.is_overall_feasible}")
    print()
    print(f"  Retirement corpus needed:   ₹{out.retirement.corpus_required_used:>15,.0f}")
    print(f"  Retirement date:            {out.retirement.retirement_date}")
    print(f"  Years to retirement:        {out.retirement.years_to_retirement:.1f}")
    print()
    print(f"  Goals ({len(out.goals)}):")
    for g in out.goals:
        flag = "✓" if g.is_funded else "✗"
        print(f"    {flag} {g.name:30s}  PV ₹{g.amount_pv:>12,.0f}  FV ₹{g.amount_fv:>14,.0f}  shortfall ₹{g.shortfall_fv:>12,.0f}")
    print()
    print(f"  One-off outflows ({len(out.one_off_outflow_status)}):")
    for o in out.one_off_outflow_status:
        flag = "✓" if o.is_funded else "✗"
        print(f"    {flag} {o.description:30s}  amount ₹{o.amount:>12,.0f}  shortfall ₹{o.shortfall:>12,.0f}")
    print()
    if out.fund_flow_summary:
        f = out.fund_flow_summary
        print(f"  Fund-flow bridge:")
        print(f"    Opening NFA:        ₹{f.opening_nfa:>15,.0f}")
        print(f"    + Investments:      ₹{f.total_investments:>15,.0f}")
        print(f"    + ROI:              ₹{f.total_roi:>15,.0f}")
        print(f"    + One-off in:       ₹{f.total_one_off_in:>15,.0f}")
        print(f"    − One-off out:      ₹{f.total_one_off_out:>15,.0f}")
        print(f"    − Goals paid:       ₹{f.total_goals_paid:>15,.0f}")
        print(f"    Closing NFA:        ₹{f.closing_nfa:>15,.0f}")
    if hasattr(out, 'goal_property_details') and out.goal_property_details:
        print(f"\n  Property details ({len(out.goal_property_details)}):")
        for d in out.goal_property_details:
            print(f"    - {d.name}: target_FV ₹{d.target_fv:,.0f}, payout_FV ₹{d.payout_amount_fv:,.0f}, "
                  f"mortgage ₹{d.mortgage_amount:,.0f}")
            if d.mortgage_emi_monthly:
                print(f"      EMI ₹{d.mortgage_emi_monthly:,.0f}/mo, total interest ₹{d.mortgage_total_interest:,.0f}, "
                      f"payoff {d.mortgage_payoff_date}")
    if hasattr(out, 'derived_stats') and out.derived_stats:
        ds = out.derived_stats
        print(f"\n  Derived stats:")
        print(f"    Peak NFA:  ₹{ds.peak_nfa_amount:>15,.0f} on {ds.peak_nfa_date}")
        print(f"    Min NFA:   ₹{ds.min_nfa_amount:>15,.0f} on {ds.min_nfa_date}")
        if ds.nfa_at_retirement is not None:
            print(f"    NFA at retirement: ₹{ds.nfa_at_retirement:,.0f}")
        print(f"    Closing NFA in today's money: ₹{ds.closing_nfa_pv:,.0f}")
        print(f"    Worst savings year: {ds.worst_savings_fy} (₹{ds.worst_savings_amount:,.0f})")
        print(f"    Best savings year:  {ds.best_savings_fy} (₹{ds.best_savings_amount:,.0f})")
        if ds.debt_free_date:
            print(f"    Debt-free date: {ds.debt_free_date}")
        if ds.months_corpus_will_last_post_retirement is not None:
            print(f"    Corpus will last: {ds.months_corpus_will_last_post_retirement} months post-retirement")
        print(f"    Goals by category:")
        for cat, agg in ds.goals_by_category.items():
            print(f"      {cat}: {agg.count} goal(s), shortfall ₹{agg.total_shortfall:,.0f}, all funded: {agg.all_funded}")
    if out.warnings:
        print()
        print(f"  Warnings ({len(out.warnings)}):")
        for w in out.warnings:
            print(f"    - {w}")
    print()


def _print_snapshot_summary(label: str, snap) -> None:
    print("=" * 70)
    print(f"AGENT SNAPSHOT: {label}")
    print("=" * 70)
    print(f"  engine_version: {snap.engine_version}")
    print(f"  headline.is_overall_feasible: {snap.headline.is_overall_feasible}")
    print(f"  headline.total_shortfall_fv: ₹{snap.headline.total_shortfall_fv:,.0f}")
    print(f"  headline.closing_nfa: ₹{snap.headline.closing_nfa:,.0f}")
    print()
    print(f"  actions_taken_this_turn ({len(snap.actions_taken_this_turn)}):")
    for j, a in enumerate(snap.actions_taken_this_turn):
        args_str = str(a.arguments)[:80]
        summary_str = a.summary[:120].replace("\n", " | ")
        print(f"    {j+1}. {a.tool_name}({args_str})")
        print(f"       → {summary_str}")
    print()
    print(f"  extracted_events_this_turn ({len(snap.extracted_events_this_turn)}):")
    for ev in snap.extracted_events_this_turn:
        print(f"    - {ev.kind}: {ev.model_dump_json()[:120]}...")
    print()
    print(f"  levers ({len(snap.levers)}):")
    for j, lev in enumerate(snap.levers):
        print(f"    {j+1}. [{lev.confidence}] {lev.description}")
    print()
    if snap.error_log:
        print(f"  error_log ({len(snap.error_log)}):")
        for e in snap.error_log:
            print(f"    - {e}")
    print()


async def main() -> int:
    print(f"Goal Planning Pipeline Probe — engine v{ENGINE_VERSION}")
    print(f"Output dir: {OUT_DIR}")
    print()

    # ====================================================================
    # Stage 1: build input + validate
    # ====================================================================
    inp = _build_realistic_input()
    _dump("01_input", inp)
    print(f"[OK] Built realistic input → {OUT_DIR}/01_input.json")

    issues = validate_input_only(inp)
    _dump("02_validation_issues", [i.model_dump() for i in issues])
    if issues:
        print(f"[WARN] {len(issues)} validation issue(s):")
        for i in issues:
            print(f"  [{i.severity}] {i.field}: {i.message}")
    else:
        print(f"[OK] No pre-flight validation issues")
    print()

    # ====================================================================
    # Stage 2: run engine
    # ====================================================================
    started = datetime.utcnow()
    out = compute_full_projection(inp)
    elapsed = (datetime.utcnow() - started).total_seconds() * 1000
    print(f"[OK] Engine ran in {elapsed:.1f}ms")
    _dump("03_engine_output", out)
    print(f"[OK] Dumped output → {OUT_DIR}/03_engine_output.json")
    print()
    _print_engine_summary(out)

    # ====================================================================
    # Stage 3: agent (only if API key available)
    # ====================================================================
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[SKIP] ANTHROPIC_API_KEY not set; skipping agent probe.")
        print()
        return 0

    print("=" * 70)
    print("AGENT PROBE (real Claude API)")
    print("=" * 70)
    print(f"  Using API key: {api_key[:20]}...{api_key[-6:]}")
    print()

    queries = [
        "Am I on track for my retirement and other financial goals?",
        "What if I retire at 58 instead of 60?",
        "Suggest 3 things I can do to close any shortfalls in my plan.",
        "If my son's abroad education ends up costing 1Cr in today's money instead of 80L, how bad does that look?",
    ]

    session_id = f"probe-{datetime.utcnow().isoformat(timespec='seconds')}"
    anchor = date(2026, 5, 9)

    all_responses = []
    for i, q in enumerate(queries, start=1):
        print(f">>> Turn {i}: {q!r}")
        print()
        try:
            request = GoalPlanningRequest(
                user_question=q,
                baseline_input=inp,
                chat_session_id=session_id,
                anchor_date=anchor,
                detail_level="full",
            )
            snap = await run_cashflow_statement(request)
            _print_snapshot_summary(f"Turn {i}", snap)
            all_responses.append({
                "turn": i,
                "user_question": q,
                "snapshot": snap.model_dump(mode="json"),
            })
            _dump(f"04_agent_turn_{i}", snap)
        except Exception as e:  # surface but don't crash the whole probe
            err = {"turn": i, "user_question": q, "error": f"{type(e).__name__}: {e}"}
            print(f"[ERROR] Turn {i} failed: {err['error']}")
            all_responses.append(err)
            _dump(f"04_agent_turn_{i}_error", err)

    _dump("05_agent_all_responses", all_responses)
    print(f"[OK] All {len(queries)} agent turns complete → see {OUT_DIR}/05_agent_all_responses.json")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
