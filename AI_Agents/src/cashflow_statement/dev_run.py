"""Cashflow Statement — Dev Test Runner.

Builds five distinct dummy profiles, runs the engine on each, and writes:
  - dev_artifacts/data.json   (list of profiles for inspection)
  - dev_artifacts/data.js     (same content as `window.__DATA__`)

The viewer renders a profile-selector at the top and swaps the active payload
on change.

Run from AI_Agents/src/:  python -m cashflow_statement.dev_run
Then open cashflow_statement/viewer.html in a browser.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

# AI_Agents/src/ on sys.path when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Auto-load Prozpr_Backend/.env so ANTHROPIC_API_KEY is available without
# manual `export`. Walks up from this file to find the first `.env`.
try:
    from dotenv import load_dotenv  # type: ignore
    _here = Path(__file__).resolve()
    for parent in _here.parents:
        candidate = parent / ".env"
        if candidate.is_file():
            # override=True so a real value in .env wins over an empty
            # ANTHROPIC_API_KEY="" inherited from a parent shell.
            load_dotenv(candidate, override=True)
            break
except ImportError:
    pass

from cashflow_statement import (
    compute_full_projection, summarize_plan,
    GoalPlanningInput, ClientProfile, RetirementInput,
    CurrentProperty, GoalProperty, CustomGoal, OneOffEvent, GoalType,
)
from cashflow_statement.agent.levers import propose_levers


ARTIFACTS_DIR = Path(__file__).parent / "dev_artifacts"


def profile_hni_multigoal() -> GoalPlanningInput:
    """HNI, 40, multi-property + 3 custom goals. Plan should comfortably fund itself."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=4_000_000, effective_tax_rate=0.25,
            financial_assets=30_000_000, financial_liabilities_excl_mortgage=1_000_000,
            monthly_household_expense=150_000, starting_monthly_investment=100_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1985, 6, 15), retirement_age=60),
        current_properties=[
            CurrentProperty(name="primary_residence", has_mortgage=True,
                            mortgage_emi=85_000, mortgage_end_date=date(2032, 6, 30)),
        ],
        goal_properties=[
            GoalProperty(name="second_home", target_pv=30_000_000, goal_date=date(2030, 4, 1),
                         is_downpayment_only=True, downpayment_pct=0.25),
            GoalProperty(name="vacation_home", target_pv=15_000_000, goal_date=date(2035, 4, 1),
                         is_downpayment_only=False),
        ],
        custom_goals=[
            CustomGoal(name="generic_custom", goal_type=GoalType.custom,
                       goal_value_pv=2_000_000, goal_date=date(2028, 9, 1)),
            CustomGoal(name="child_abroad_education", goal_type=GoalType.child_abroad_education,
                       goal_value_pv=5_000_000, goal_date=date(2038, 7, 1)),
            CustomGoal(name="child_marriage", goal_type=GoalType.child_marriage,
                       goal_value_pv=3_000_000, goal_date=date(2042, 12, 1)),
        ],
        one_off_inflows=[OneOffEvent(description="bonus_2027", amount=1_500_000, date=date(2027, 3, 31))],
        one_off_outflows=[OneOffEvent(description="medical_2029", amount=1_000_000, date=date(2029, 6, 30))],
        detail_level="full",
    )


def profile_young_saver() -> GoalPlanningInput:
    """28yo professional, small corpus, long horizon — retirement + far-out child education only."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=1_500_000, effective_tax_rate=0.15,
            financial_assets=500_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=40_000, starting_monthly_investment=25_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1998, 3, 10), retirement_age=60),
        current_properties=[],
        goal_properties=[],
        custom_goals=[
            CustomGoal(name="child_abroad_education", goal_type=GoalType.child_abroad_education,
                       goal_value_pv=4_000_000, goal_date=date(2046, 6, 1)),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_mid_career_family() -> GoalPlanningInput:
    """38yo, mortgaged home, two children (education + marriage), eyeing a second home."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=2_500_000, effective_tax_rate=0.22,
            financial_assets=8_000_000, financial_liabilities_excl_mortgage=300_000,
            monthly_household_expense=90_000, starting_monthly_investment=60_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1988, 1, 20), retirement_age=60),
        current_properties=[
            CurrentProperty(name="primary_residence", has_mortgage=True,
                            mortgage_emi=55_000, mortgage_end_date=date(2036, 1, 31)),
        ],
        goal_properties=[
            GoalProperty(name="second_home", target_pv=12_000_000, goal_date=date(2034, 4, 1),
                         is_downpayment_only=True, downpayment_pct=0.20),
        ],
        custom_goals=[
            CustomGoal(name="child1_education", goal_type=GoalType.child_local_education,
                       goal_value_pv=3_000_000, goal_date=date(2034, 7, 1)),
            CustomGoal(name="child2_education", goal_type=GoalType.child_local_education,
                       goal_value_pv=3_000_000, goal_date=date(2037, 7, 1)),
            CustomGoal(name="child1_marriage", goal_type=GoalType.child_marriage,
                       goal_value_pv=2_500_000, goal_date=date(2044, 11, 1)),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_pre_retiree() -> GoalPlanningInput:
    """55yo HNI, retirement-focused, modest aspirational goal."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=5_000_000, effective_tax_rate=0.28,
            financial_assets=40_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=200_000, starting_monthly_investment=150_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1971, 9, 5), retirement_age=62),
        current_properties=[],
        goal_properties=[],
        custom_goals=[
            CustomGoal(name="world_tour", goal_type=GoalType.custom,
                       goal_value_pv=2_500_000, goal_date=date(2033, 12, 1)),
        ],
        one_off_inflows=[OneOffEvent(description="property_sale", amount=8_000_000, date=date(2028, 6, 30))],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_stretched_aspirer() -> GoalPlanningInput:
    """32yo modest income chasing ambitious goals — plan likely shows shortfalls."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=1_800_000, effective_tax_rate=0.18,
            financial_assets=1_500_000, financial_liabilities_excl_mortgage=200_000,
            monthly_household_expense=60_000, starting_monthly_investment=20_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1994, 7, 22), retirement_age=60),
        current_properties=[],
        goal_properties=[
            GoalProperty(name="first_home", target_pv=15_000_000, goal_date=date(2030, 4, 1),
                         is_downpayment_only=True, downpayment_pct=0.20),
        ],
        custom_goals=[
            CustomGoal(name="child_abroad_education", goal_type=GoalType.child_abroad_education,
                       goal_value_pv=6_000_000, goal_date=date(2042, 7, 1)),
            CustomGoal(name="emergency_buffer", goal_type=GoalType.custom,
                       goal_value_pv=1_000_000, goal_date=date(2028, 4, 1)),
        ],
        one_off_inflows=[],
        one_off_outflows=[OneOffEvent(description="parent_medical_2031",
                                       amount=800_000, date=date(2031, 9, 30))],
        detail_level="full",
    )


PROFILES: list[tuple[str, str, callable]] = [
    ("hni_multigoal",     "HNI · Multi-goal (40yo)",          profile_hni_multigoal),
    ("young_saver",       "Young Saver (28yo)",               profile_young_saver),
    ("mid_career_family", "Mid-Career Family (38yo)",         profile_mid_career_family),
    ("pre_retiree",       "Pre-Retiree (55yo, HNI)",          profile_pre_retiree),
    ("stretched_aspirer", "Stretched Aspirer (32yo)",         profile_stretched_aspirer),
]


def build_payload(inp: GoalPlanningInput, with_summary: bool) -> dict:
    out = compute_full_projection(inp)
    out_dict = out.model_dump(mode="json")
    payload = {
        "engine_version": out_dict.pop("engine_version"),
        "computed_at": out_dict.pop("computed_at"),
        "input": inp.model_dump(mode="json"),
        **out_dict,
    }
    payload.pop("input_echo", None)
    if with_summary:
        try:
            levers = propose_levers(inp, out)
            payload["summary"] = summarize_plan(out, levers=levers).model_dump(mode="json")
        except Exception as e:
            payload["summary_error"] = str(e)
    return payload


def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with_summary = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not with_summary:
        print("  (ANTHROPIC_API_KEY not set — skipping LLM summaries)")
    profiles_out = []
    for pid, label, factory in PROFILES:
        payload = build_payload(factory(), with_summary=with_summary)
        profiles_out.append({"id": pid, "label": label, "payload": payload})
        print(f"  · {label}: closing corpus "
              f"{payload['headline']['corpus_closing']:,.0f}, "
              f"shortfall {payload['headline']['total_shortfall_fv']:,.0f}"
              + (" [summary ✓]" if payload.get("summary") else ""))

    bundle = {"profiles": profiles_out}
    json_text = json.dumps(bundle, indent=2)
    (ARTIFACTS_DIR / "data.json").write_text(json_text, encoding="utf-8")
    (ARTIFACTS_DIR / "data.js").write_text(
        f"window.__DATA__ = {json_text};\n", encoding="utf-8"
    )
    print(f"OK. Wrote {len(profiles_out)} profiles to {ARTIFACTS_DIR}")


if __name__ == "__main__":
    main()
