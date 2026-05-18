"""DEV-ONLY: Cross-validate the engine against a Python emulator of Sourabh's Excel.

Defines a fresh "profile 2" scenario (different shape from Sourabh's baseline) and runs
it through both:

  - the engine (`compute_full_projection`)
  - the emulator (`excel_emulator.emulate`) — implements Excel's documented formulas

Then diffs the outputs cell-by-cell and produces a report. The point is to confirm that
the engine's documented divergences from Excel (Causes A–D, post-retirement invest, etc.)
appear consistently on a different scenario — i.e., the divergences are intrinsic to the
formula differences, not artifacts of the baseline data.

Run from repo root:
    .venv-mac/bin/python scripts/validate_engine_vs_emulator.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "AI_Agents" / "src"))
sys.path.insert(0, str(REPO_ROOT / "AI_Agents" / "src" / "cashflow_statement" / "tests" / "fixtures" / "excel_reference"))

from cashflow_statement import (
    GoalPlanningInput, Assumptions, ClientProfile, RetirementInput,
    CurrentProperty, GoalProperty, CustomGoal, OneOffEvent, GoalType,
    compute_full_projection,
)
from excel_emulator import emulate


# ---------------------------------------------------------------------------
# Profile 2 — mid-career professional with one realistic existing mortgage
# (no negative-rate edge case, no past as_of_date) plus one downpayment goal.
# Deliberately different from baseline: smaller starting NFA, positive income,
# fewer goals, shorter horizon.
# ---------------------------------------------------------------------------

def build_profile_2() -> GoalPlanningInput:
    return GoalPlanningInput(
        assumptions=Assumptions(),  # defaults
        profile=ClientProfile(
            latest_update_date=date(2026, 4, 1),         # FY-aligned (no partial first FY)
            annual_income=3_000_000,                      # ₹30L per year
            tax_rate=0.30,
            financial_assets=5_000_000,                   # ₹50L (smaller than baseline's 2Cr)
            financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000,
            monthly_investment_next_12m=100_000,
        ),
        retirement=RetirementInput(
            date_of_birth=date(1989, 1, 15),              # age ~37 at update
            retirement_age=60,
            assumed_total_age=85,
        ),
        current_properties=[
            CurrentProperty(
                name="primary_home", has_mortgage=True,
                mortgage_balance=6_000_000,               # ₹60L outstanding
                mortgage_emi=50_000,                      # ₹50K/month
                mortgage_interest_annual=0.085,           # 8.5% — realistic
            ),
        ],
        goal_properties=[
            GoalProperty(
                name="vacation_home", target_pv=8_000_000,
                is_downpayment_only=True, upfront_amount=1_500_000,
                goal_date=date(2030, 6, 1),
                mortgage_tenure_years=20, mortgage_interest_annual=0.0875,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="child_education", goal_type=GoalType.child_local_education,
                amount_pv=2_000_000, goal_date=date(2042, 6, 1),
            ),
            CustomGoal(
                name="child_marriage", goal_type=GoalType.child_marriage,
                amount_pv=3_000_000, goal_date=date(2050, 12, 1),
            ),
        ],
        one_off_inflows=[
            OneOffEvent(description="bonus_2028", amount=500_000, date=date(2028, 3, 15)),
        ],
        one_off_outflows=[
            OneOffEvent(description="car_2031", amount=1_000_000, date=date(2031, 6, 1)),
        ],
        detail_level="full",
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

REL_TOL = 0.005   # 0.5%
ABS_TOL = 100.0


def _close(a, b):
    if a is None or b is None:
        return a == b
    diff = float(a) - float(b)
    return abs(diff) <= max(ABS_TOL, REL_TOL * abs(float(b)))


def _format_cell_compare(label, engine_val, emu_val, suspected_cause=""):
    if engine_val is None and emu_val is None:
        return None
    try:
        engine_f = float(engine_val) if engine_val is not None else None
        emu_f = float(emu_val) if emu_val is not None else None
    except (TypeError, ValueError):
        # Date or string — direct compare
        match = "✓" if str(engine_val) == str(emu_val) else "✗"
        return f"  {match} {label:<45} engine={engine_val}  emu={emu_val}  {suspected_cause}"
    if engine_f is None or emu_f is None:
        return f"  ? {label:<45} engine={engine_val}  emu={emu_val}"
    diff = engine_f - emu_f
    rel = abs(diff) / max(abs(emu_f), 1)
    match = "✓" if _close(engine_f, emu_f) else "✗"
    return (f"  {match} {label:<45} engine={engine_f:>15,.0f}  emu={emu_f:>15,.0f}  "
            f"diff={diff:>+13,.0f} ({rel:>6.2%})  {suspected_cause}")


def main() -> int:
    inp = build_profile_2()
    engine_out = compute_full_projection(inp)
    emu_out = emulate(inp)

    print("=" * 100)
    print("ENGINE vs EMULATOR — Profile 2 (mid-career professional)")
    print("=" * 100)
    print()

    # ----- HEADLINE -----
    print("HEADLINE")
    h = engine_out.headline
    rows = [
        ("net_financial_assets_today", h.net_financial_assets_today, emu_out["B26"], ""),
        ("number_of_goals", h.number_of_goals, emu_out["B86"], ""),
        ("horizon_years", h.horizon_years, emu_out["B89"], ""),
        ("last_fy_end_date", h.last_fy_end_date, emu_out["B88"], ""),
        ("sum_fund_today_pv", h.sum_fund_today_pv, emu_out["O113"], ""),
        ("present_status", h.present_status, emu_out["S105"], ""),
        ("total_funded_amount", h.total_funded_amount, emu_out["M113"], ""),
        ("total_shortfall_fv", h.total_shortfall_fv, emu_out["L113"], ""),
        ("closing_nfa", h.closing_nfa, emu_out["S214"],
         "← Cause D (engine compound monthly vs emu annual simple ROI)"),
    ]
    for label, e, em, note in rows:
        line = _format_cell_compare(label, e, em, note)
        if line:
            print(line)

    # ----- RETIREMENT -----
    print("\nRETIREMENT")
    r = engine_out.retirement
    for label, e, em, note in [
        ("retirement_date", r.retirement_date, emu_out["B39"], ""),
        ("annual_expense_FV", r.annual_household_expense_at_retirement, emu_out["B42"], ""),
        ("corpus_required_computed", r.corpus_required_computed, emu_out["B43"], ""),
        ("corpus_required_used", r.corpus_required_used, emu_out["B46"], ""),
    ]:
        line = _format_cell_compare(label, e, em, note)
        if line:
            print(line)

    # ----- PER-GOAL -----
    print("\nPER-GOAL (amount_fv, fund_today_pv, funded_amount, shortfall_fv)")
    engine_goals = {g.name: g for g in engine_out.goals}
    emu_goals = {g["name"]: g for g in emu_out["_goals"]}
    for name in sorted(set(engine_goals) | set(emu_goals)):
        eg = engine_goals.get(name)
        em = emu_goals.get(name)
        if eg is None:
            print(f"  ✗ {name:<30} engine: MISSING  emu: present")
            continue
        if em is None:
            print(f"  ✗ {name:<30} engine: present  emu: MISSING")
            continue
        for f in ["amount_fv", "fund_today_pv", "funded_amount", "shortfall_fv"]:
            line = _format_cell_compare(
                f"{name}.{f}", getattr(eg, f), em.get(f),
            )
            if line:
                print(line)

    # ----- FUND FLOW BRIDGE -----
    print("\nFUND FLOW BRIDGE (engine vs emulator-as-Excel)")
    f = engine_out.fund_flow_summary
    for label, e, em, note in [
        ("opening_nfa", f.opening_nfa, emu_out["S93"], ""),
        ("total_investments", f.total_investments, emu_out["S94"], "← engine includes net (drawdowns); emu sums signed M"),
        ("total_roi", f.total_roi, emu_out["S95"],
         "← Cause D: engine compound monthly, emu annual simple"),
        ("total_one_off_in", f.total_one_off_in, emu_out["S96"], ""),
        ("total_one_off_out", f.total_one_off_out, -emu_out["S97"], ""),
        ("total_goals_paid", f.total_goals_paid, -emu_out["S98"], ""),
        ("closing_nfa (bridge)", f.closing_nfa, -emu_out["S99"],
         "← compounds Cause D drift"),
    ]:
        line = _format_cell_compare(label, e, em, note)
        if line:
            print(line)

    # ----- ANNUAL CASHFLOW (per-FY summary) -----
    print("\nANNUAL CASHFLOW (sample FYs: 1, 2, retirement, retirement+1, last)")
    engine_annual = {a.fy_end_date: a for a in engine_out.annual_cashflow}
    emu_annual = {row["fy_end_date"]: row for row in emu_out["_annual_cashflow"]}
    sample_fys = sorted(set(engine_annual) | set(emu_annual))
    ret_fy = date(r.retirement_date.year + (1 if r.retirement_date.month > 3 else 0), 3, 31)
    samples = [sample_fys[0], sample_fys[1], ret_fy] if ret_fy in sample_fys else sample_fys[:3]
    if ret_fy in sample_fys:
        idx = sample_fys.index(ret_fy)
        if idx + 1 < len(sample_fys):
            samples.append(sample_fys[idx + 1])
    samples.append(sample_fys[-1])
    samples = sorted(set(samples))
    for fy in samples:
        if fy not in engine_annual or fy not in emu_annual:
            continue
        ea = engine_annual[fy]
        em = emu_annual[fy]
        print(f"\n  -- FY {fy} --")
        for f in ["income", "income_tax", "household_expense", "savings_1",
                  "existing_mortgage_emi_total", "goal_mortgage_emi_total",
                  "savings_2", "investment_amount", "one_off_in", "one_off_out"]:
            note = ""
            if f == "investment_amount" and fy.year > r.retirement_date.year:
                note = "← post-retirement: engine still annualises sip; emu zero (Excel matches emu)"
            elif f == "existing_mortgage_emi_total":
                note = "← Cause C: engine compound monthly, emu annual simple"
            line = _format_cell_compare(f"  {f}", getattr(ea, f), em[f], note)
            if line:
                print(line)

    # ----- DIVERGENCE ROLLUP -----
    print("\n" + "=" * 100)
    print("DIVERGENCE SUMMARY (cells where engine ≠ emulator beyond tolerance)")
    print("=" * 100)
    divergences = _collect_all_divergences(engine_out, emu_out, r.retirement_date)
    if not divergences:
        print("  None — engine fully matches emulator within tolerance.")
    else:
        print(f"  Total: {len(divergences)} cells")
        by_cause: dict[str, list] = {}
        for d in divergences:
            by_cause.setdefault(d["cause"], []).append(d)
        for cause, items in sorted(by_cause.items(), key=lambda x: -len(x[1])):
            print(f"\n  [{cause}]  ({len(items)} cells)")
            for d in items[:8]:
                print(f"    {d['label']:<50} engine={d['engine']:>15,.0f}  "
                      f"emu={d['emu']:>15,.0f}  diff={d['diff']:>+13,.0f} ({d['pct']:>+6.2%})")
            if len(items) > 8:
                print(f"    ... and {len(items)-8} more")

    return 0


def _collect_all_divergences(engine_out, emu_out, retirement_date) -> list[dict]:
    """Walk every comparable cell, return a list of mismatch records with attributed cause."""
    out: list[dict] = []
    h = engine_out.headline
    f = engine_out.fund_flow_summary
    r = engine_out.retirement

    def maybe_add(label, e, em, cause):
        try:
            ef, emf = float(e), float(em)
        except (TypeError, ValueError):
            return
        diff = ef - emf
        rel = abs(diff) / max(abs(emf), 1)
        if abs(diff) > max(ABS_TOL, REL_TOL * abs(emf)):
            out.append({
                "label": label, "engine": ef, "emu": emf, "diff": diff,
                "pct": rel * (1 if diff >= 0 else -1), "cause": cause,
            })

    maybe_add("headline.closing_nfa", h.closing_nfa, emu_out["S214"], "D: ROI compounding")
    maybe_add("headline.total_funded_amount", h.total_funded_amount, emu_out["M113"], "(small rounding)")
    maybe_add("headline.sum_fund_today_pv", h.sum_fund_today_pv, emu_out["O113"], "(small rounding)")
    maybe_add("headline.present_status", h.present_status, emu_out["S105"], "(small rounding)")
    maybe_add("retirement.corpus_required_computed", r.corpus_required_computed, emu_out["B43"],
              "(should match)")
    maybe_add("retirement.corpus_required_used", r.corpus_required_used, emu_out["B46"],
              "(should match)")
    maybe_add("retirement.annual_expense_FV", r.annual_household_expense_at_retirement,
              emu_out["B42"], "(should match)")
    maybe_add("fund_flow.total_investments", f.total_investments, emu_out["S94"],
              "(timing of M sum)")
    maybe_add("fund_flow.total_roi", f.total_roi, emu_out["S95"], "D: ROI compounding")
    maybe_add("fund_flow.closing_nfa", f.closing_nfa, -emu_out["S99"], "D: ROI compounding")

    # Per-FY annual cashflow
    engine_annual = {a.fy_end_date: a for a in engine_out.annual_cashflow}
    emu_annual = {row["fy_end_date"]: row for row in emu_out["_annual_cashflow"]}
    for fy in sorted(set(engine_annual) | set(emu_annual)):
        if fy not in engine_annual or fy not in emu_annual:
            continue
        ea = engine_annual[fy]
        em = emu_annual[fy]
        for field in ["income", "income_tax", "household_expense", "savings_1",
                       "existing_mortgage_emi_total", "goal_mortgage_emi_total",
                       "savings_2", "investment_amount", "one_off_in", "one_off_out"]:
            cause = ""
            if field == "existing_mortgage_emi_total":
                cause = "C: mortgage compound monthly vs annual simple"
            elif field == "investment_amount" and fy.year >= retirement_date.year:
                # Retirement year (engine SIP × 12 vs emu K-based) and post-retirement
                # (engine non-zero vs emu zero) — same root cause: engine's annual
                # investment_amount is gross display, doesn't reflect retirement transition.
                cause = "POST-RETIREMENT semantic"
            elif field == "savings_2":
                cause = "C cascade (mortgage → savings)"
            else:
                cause = "(unexpected)"
            maybe_add(f"FY{fy.year}.{field}", getattr(ea, field), em[field], cause)

    # Per-goal
    engine_goals = {g.name: g for g in engine_out.goals}
    emu_goals = {g["name"]: g for g in emu_out["_goals"]}
    for name in set(engine_goals) & set(emu_goals):
        eg, em = engine_goals[name], emu_goals[name]
        for field in ["amount_fv", "fund_today_pv", "funded_amount", "shortfall_fv"]:
            maybe_add(f"goal:{name}.{field}", getattr(eg, field), em[field], "(should match)")

    return out


if __name__ == "__main__":
    sys.exit(main())
