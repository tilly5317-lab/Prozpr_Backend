"""
Customer Test Data
==================
Dummy input profiles for 5 real customers to test the risk profiling module.

Run from src/:  python -m risk_profiling.customer_test_data
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

from risk_profiling.main import risk_profiling_chain
from risk_profiling.scoring import compute_all_scores

# ── 5 Customer Profiles ────────────────────────────────────────────────────────
#
# All monetary values are in INR (₹).
# Fields:
#   investor_name         – for display only (not a RiskProfileInput field)
#   age                   – integer
#   occupation_type       – one of: public_sector | private_sector |
#                           family_business | commission_based |
#                           freelancer_gig | retired_homemaker_student
#   annual_income         – gross annual income (₹)
#   annual_expense        – total yearly household spend (₹)
#   financial_assets      – liquid/investable assets: MF, FD, equities, cash (₹)
#   liabilities_excluding_mortgage  – personal loans, car loans, credit card dues (₹)
#   annual_mortgage_payment         – yearly EMIs for home loan (₹)
#   properties_owned      – 0, 1, or >1
#   risk_willingness      – self-reported score 1-10
#
# This module computes the effective risk score from these inputs; the score
# is an OUTPUT here, so it is intentionally not pinned per profile.

CUSTOMER_PROFILES = [
    # ── 1. Aarav Gupta — Bangalore, Karnataka ─────────────────────────────────
    # Software engineer at a startup, single, high saver, very aggressive.
    {
        "investor_name": "Aarav Gupta",
        "age": 26,
        "occupation_type": "private_sector",
        "annual_income": 1_800_000,  # ₹18 LPA (startup SDE)
        "annual_expense": 600_000,  # ₹50k/month, single
        "financial_assets": 450_000,  # just started investing
        "liabilities_excluding_mortgage": 50_000,  # credit card dues
        "annual_mortgage_payment": 0,  # renting
        "properties_owned": 0,
        "risk_willingness": 9.5,  # very high — long horizon, no dependents
    },
    # ── 2. Lakshmi Iyer — Chennai, Tamil Nadu ─────────────────────────────────
    # Retired bank manager and widow, lives off her corpus, capital preservation.
    {
        "investor_name": "Lakshmi Iyer",
        "age": 58,
        "occupation_type": "retired_homemaker_student",
        "annual_income": 600_000,  # ₹6 LPA (pension)
        "annual_expense": 480_000,  # ₹40k/month
        "financial_assets": 9_500_000,  # retirement corpus
        "liabilities_excluding_mortgage": 0,  # debt-free
        "annual_mortgage_payment": 0,  # owns her home outright
        "properties_owned": 1,
        "risk_willingness": 2.0,  # very low — capital preservation
    },
    # ── 3. Mohammed Faisal — Hyderabad, Telangana ─────────────────────────────
    # Government school teacher, married with two children, moderate risk.
    {
        "investor_name": "Mohammed Faisal",
        "age": 41,
        "occupation_type": "public_sector",
        "annual_income": 900_000,  # ₹9 LPA (teacher)
        "annual_expense": 540_000,  # ₹45k/month household
        "financial_assets": 1_800_000,  # PF + FDs + some MF
        "liabilities_excluding_mortgage": 100_000,  # small personal loan
        "annual_mortgage_payment": 0,  # renting, home purchase still a goal
        "properties_owned": 0,
        "risk_willingness": 5.5,  # balanced
    },
    # ── 4. Neha Reddy — Mumbai, Maharashtra ───────────────────────────────────
    # Entrepreneur running a D2C skincare brand, irregular but growing income.
    {
        "investor_name": "Neha Reddy",
        "age": 35,
        "occupation_type": "family_business",
        "annual_income": 2_500_000,  # ₹25 LPA (business income, growing)
        "annual_expense": 1_080_000,  # ₹90k/month
        "financial_assets": 5_200_000,  # business surpluses invested
        "liabilities_excluding_mortgage": 300_000,  # business credit line
        "annual_mortgage_payment": 0,  # renting
        "properties_owned": 0,
        "risk_willingness": 8.0,  # growth-oriented
    },
    # ── 5. Harpreet Singh — Ludhiana, Punjab ──────────────────────────────────
    # Logistics business owner, married with one child in college, de-risking.
    {
        "investor_name": "Harpreet Singh",
        "age": 49,
        "occupation_type": "family_business",
        "annual_income": 3_500_000,  # ₹35 LPA (business profit)
        "annual_expense": 960_000,  # ₹80k/month
        "financial_assets": 6_800_000,  # business surpluses + MF + FD
        "liabilities_excluding_mortgage": 200_000,  # vehicle loan balance
        "annual_mortgage_payment": 240_000,  # ₹20k/month home EMI
        "properties_owned": 1,
        "risk_willingness": 4.0,  # moderate-conservative, nearing peak earning
    },
]


def _strip_meta(profile: dict) -> dict:
    """Return only the fields required by RiskProfileInput."""
    exclude = {"investor_name"}
    return {k: v for k, v in profile.items() if k not in exclude}


# ── Runner ─────────────────────────────────────────────────────────────────────


def main(run_llm: bool = True) -> None:
    _AGENTS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    load_dotenv(os.path.join(_AGENTS_ROOT, ".env"))

    print("=" * 80)
    print("RISK PROFILING — 5 Customer Profiles")
    print("=" * 80)

    all_results = []

    for profile in CUSTOMER_PROFILES:
        name = profile["investor_name"]
        inputs = _strip_meta(profile)

        print(f"\n{'─' * 60}")
        print(f"  Customer : {name}")
        print(
            f"  Age      : {profile['age']}   Occupation: {profile['occupation_type']}"
        )
        print(
            f"  Income   : ₹{profile['annual_income']:,.0f}   Willingness: {profile['risk_willingness']}/10"
        )
        print(f"{'─' * 60}")

        if run_llm:
            result = risk_profiling_chain.invoke(inputs)
        else:
            result = compute_all_scores(inputs)
            result["output"]["risk_summary"] = "(LLM summary skipped)"

        calc = result["calculations"]
        out = result["output"]

        print(f"  Effective Risk Score : {out['effective_risk_score']:.4f}")
        print(f"  Risk Capacity Score  : {calc['risk_capacity_score_clamped']:.4f}")
        print(
            f"  Willingness-Cap Gap  : {calc['willingness_capacity_gap']:.4f}  (>3: {calc['gap_exceeds_3']})"
        )
        print(f"  Savings Adjustment   : {calc['savings_rate_adjustment']}")
        print(
            f"  Was Clamped          : {calc['was_clamped']}  ({calc['clamp_direction']})"
        )

        if run_llm and out.get("risk_summary"):
            print("\n  Risk Summary:")
            for line in out["risk_summary"].split("\n"):
                print(f"    {line}")

        all_results.append({"customer": name, **result})

    # Save results
    output_path = os.path.join(os.path.dirname(__file__), "customer_test_output.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n\nFull results saved to: {output_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM summary (math scoring only, no API key needed)",
    )
    args = parser.parse_args()
    main(run_llm=not args.no_llm)
