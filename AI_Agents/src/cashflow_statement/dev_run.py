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
    compute_full_projection,
    summarize_plan,
    GoalPlanningInput,
    ClientProfile,
    RetirementInput,
    CurrentProperty,
    GoalProperty,
    CustomGoal,
    OneOffEvent,
    GoalType,
)
from cashflow_statement.agent.levers import propose_levers


ARTIFACTS_DIR = Path(__file__).parent / "dev_artifacts"


# The five personas below mirror the canonical asset-allocation fixture
# (Aarav, Lakshmi, Mohammed, Neha, Harpreet). Profile/income/expense/corpus and
# the per-goal amounts match; the cashflow-specific extras are derived: DOB from
# age, horizons converted to goal_dates as of mid-2026, and "Retirement" handled
# structurally via RetirementInput rather than as a custom goal.


def profile_aarav_gupta() -> GoalPlanningInput:
    """26, Bangalore software engineer, single, aggressive. Tiny corpus vs very
    large goals → heavy shortfalls."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=1_800_000,
            effective_tax_rate=0.30,
            financial_assets=450_000,
            financial_liabilities_excl_mortgage=50_000,
            monthly_household_expense=50_000,
            starting_monthly_investment=60_000,
        ),
        retirement=RetirementInput(
            date_of_birth=date(2000, 1, 1),
            retirement_age=60,
            assumed_lifespan_years=85,
        ),
        current_properties=[],
        goal_properties=[
            GoalProperty(
                name="first_home",
                target_pv=12_000_000,
                goal_date=date(2036, 6, 1),
                is_downpayment_only=True,
                downpayment_pct=0.20,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="buy_a_car",
                goal_type=GoalType.custom,
                goal_value_pv=1_200_000,
                goal_date=date(2028, 6, 1),
            ),
            CustomGoal(
                name="wedding",
                goal_type=GoalType.custom,
                goal_value_pv=1_500_000,
                goal_date=date(2031, 6, 1),
            ),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_lakshmi_iyer() -> GoalPlanningInput:
    """58, retired bank manager and widow, lives off her corpus. Short horizons,
    goals exceeding corpus → capital preservation under funding pressure."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=600_000,
            effective_tax_rate=0.10,
            financial_assets=9_500_000,
            financial_liabilities_excl_mortgage=0,
            monthly_household_expense=40_000,
            starting_monthly_investment=0,
        ),
        retirement=RetirementInput(
            date_of_birth=date(1968, 1, 1),
            retirement_age=60,
            assumed_lifespan_years=85,
        ),
        current_properties=[],
        goal_properties=[
            GoalProperty(
                name="retirement_home",
                target_pv=5_000_000,
                goal_date=date(2028, 6, 1),
                is_downpayment_only=False,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="medical_contingency",
                goal_type=GoalType.custom,
                goal_value_pv=1_500_000,
                goal_date=date(2026, 12, 1),
            ),
            CustomGoal(
                name="retirement_income_corpus",
                goal_type=GoalType.custom,
                goal_value_pv=8_000_000,
                goal_date=date(2027, 6, 1),
            ),
            CustomGoal(
                name="grandchild_education_gift",
                goal_type=GoalType.custom,
                goal_value_pv=1_000_000,
                goal_date=date(2031, 6, 1),
            ),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_mohammed_faisal() -> GoalPlanningInput:
    """41, Hyderabad government school teacher, married with two children. Six-goal
    household across short/medium/long horizons — many-goal stress test."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=900_000,
            effective_tax_rate=0.10,
            financial_assets=1_800_000,
            financial_liabilities_excl_mortgage=100_000,
            monthly_household_expense=45_000,
            starting_monthly_investment=25_000,
        ),
        retirement=RetirementInput(
            date_of_birth=date(1985, 1, 1),
            retirement_age=60,
            assumed_lifespan_years=85,
        ),
        current_properties=[],
        goal_properties=[
            GoalProperty(
                name="home_purchase",
                target_pv=4_500_000,
                goal_date=date(2036, 6, 1),
                is_downpayment_only=True,
                downpayment_pct=0.20,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="emergency_fund",
                goal_type=GoalType.custom,
                goal_value_pv=300_000,
                goal_date=date(2026, 12, 1),
            ),
            CustomGoal(
                name="pilgrimage_hajj",
                goal_type=GoalType.custom,
                goal_value_pv=400_000,
                goal_date=date(2030, 6, 1),
            ),
            CustomGoal(
                name="child_education",
                goal_type=GoalType.child_local_education,
                goal_value_pv=4_000_000,
                goal_date=date(2034, 6, 1),
            ),
            CustomGoal(
                name="daughter_marriage",
                goal_type=GoalType.child_marriage,
                goal_value_pv=2_500_000,
                goal_date=date(2038, 6, 1),
            ),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_neha_reddy() -> GoalPlanningInput:
    """35, entrepreneur running a D2C skincare brand, irregular but growing income.
    Large near-term business buffer competing with long-term goals."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=2_500_000,
            effective_tax_rate=0.30,
            financial_assets=5_200_000,
            financial_liabilities_excl_mortgage=300_000,
            monthly_household_expense=90_000,
            starting_monthly_investment=80_000,
        ),
        retirement=RetirementInput(
            date_of_birth=date(1991, 1, 1),
            retirement_age=60,
            assumed_lifespan_years=85,
        ),
        current_properties=[],
        goal_properties=[
            GoalProperty(
                name="home_purchase",
                target_pv=15_000_000,
                goal_date=date(2032, 6, 1),
                is_downpayment_only=True,
                downpayment_pct=0.25,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="business_expansion_buffer",
                goal_type=GoalType.custom,
                goal_value_pv=2_000_000,
                goal_date=date(2027, 12, 1),
            ),
            CustomGoal(
                name="luxury_car",
                goal_type=GoalType.custom,
                goal_value_pv=2_500_000,
                goal_date=date(2029, 6, 1),
            ),
            CustomGoal(
                name="child_education_abroad",
                goal_type=GoalType.child_abroad_education,
                goal_value_pv=12_000_000,
                goal_date=date(2041, 6, 1),
            ),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


def profile_harpreet_singh() -> GoalPlanningInput:
    """49, Ludhiana logistics business owner, married with one child in college.
    Moderate-conservative; two competing property goals across medium horizons."""
    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=3_500_000,
            effective_tax_rate=0.30,
            financial_assets=6_800_000,
            financial_liabilities_excl_mortgage=200_000,
            monthly_household_expense=80_000,
            starting_monthly_investment=100_000,
        ),
        retirement=RetirementInput(
            date_of_birth=date(1977, 1, 1),
            retirement_age=60,
            assumed_lifespan_years=85,
        ),
        current_properties=[
            CurrentProperty(
                name="primary_residence",
                has_mortgage=True,
                mortgage_emi=20_000,
                mortgage_end_date=date(2032, 1, 31),
            ),
        ],
        goal_properties=[
            GoalProperty(
                name="home_upgrade",
                target_pv=6_000_000,
                goal_date=date(2032, 6, 1),
                is_downpayment_only=True,
                downpayment_pct=0.40,
            ),
            GoalProperty(
                name="rental_property",
                target_pv=8_000_000,
                goal_date=date(2034, 6, 1),
                is_downpayment_only=True,
                downpayment_pct=0.25,
            ),
        ],
        custom_goals=[
            CustomGoal(
                name="child_higher_studies",
                goal_type=GoalType.child_local_education,
                goal_value_pv=3_500_000,
                goal_date=date(2029, 6, 1),
            ),
            CustomGoal(
                name="child_marriage",
                goal_type=GoalType.child_marriage,
                goal_value_pv=3_000_000,
                goal_date=date(2033, 6, 1),
            ),
        ],
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="full",
    )


PROFILES: list[tuple[str, str, callable]] = [
    ("aarav_gupta", "Aarav Gupta · Aggressive Starter (26)", profile_aarav_gupta),
    ("lakshmi_iyer", "Lakshmi Iyer · Retired Widow (58)", profile_lakshmi_iyer),
    ("mohammed_faisal", "Mohammed Faisal · Teacher, 6 goals (41)", profile_mohammed_faisal),
    ("neha_reddy", "Neha Reddy · Entrepreneur (35)", profile_neha_reddy),
    ("harpreet_singh", "Harpreet Singh · Business Owner (49)", profile_harpreet_singh),
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
            payload["summary"] = summarize_plan(out, levers=levers).model_dump(
                mode="json"
            )
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
        print(
            f"  · {label}: closing corpus "
            f"{payload['headline']['corpus_closing']:,.0f}, "
            f"shortfall {payload['headline']['total_shortfall_fv']:,.0f}"
            + (" [summary ✓]" if payload.get("summary") else "")
        )

    bundle = {"profiles": profiles_out}
    json_text = json.dumps(bundle, indent=2)
    (ARTIFACTS_DIR / "data.json").write_text(json_text, encoding="utf-8")
    (ARTIFACTS_DIR / "data.js").write_text(
        f"window.__DATA__ = {json_text};\n", encoding="utf-8"
    )
    print(f"OK. Wrote {len(profiles_out)} profiles to {ARTIFACTS_DIR}")


if __name__ == "__main__":
    main()
