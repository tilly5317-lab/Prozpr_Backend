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

CUSTOMER_PROFILES = [
    # ── 1. Rajesh Sharma — Kolkata, West Bengal ────────────────────────────────
    # Mid-career government bank officer, stable income, one flat purchased.
    {
        "investor_name": "Rajesh Sharma",
        "age": 45,
        "occupation_type": "public_sector",
        "annual_income": 1_200_000,        # ₹12 LPA (grade-B officer)
        "annual_expense": 720_000,         # ₹60k/month household
        "financial_assets": 3_500_000,     # PF + FDs + some MF
        "liabilities_excluding_mortgage": 150_000,   # small car loan balance
        "annual_mortgage_payment": 180_000,          # ₹15k/month home EMI
        "properties_owned": 1,
        "risk_willingness": 6.0,           # moderate — comfortable but cautious
    },

    # ── 2. Priya Menon — Bangalore, Karnataka ─────────────────────────────────
    # Software engineer in a mid-size IT firm, high saver, no property yet.
    {
        "investor_name": "Priya Menon",
        "age": 31,
        "occupation_type": "private_sector",
        "annual_income": 1_800_000,        # ₹18 LPA (senior SDE)
        "annual_expense": 900_000,         # ₹75k/month (rent + lifestyle)
        "financial_assets": 1_400_000,     # MF SIPs + ESOP value
        "liabilities_excluding_mortgage": 80_000,    # credit card dues
        "annual_mortgage_payment": 0,                # renting, no home loan
        "properties_owned": 0,
        "risk_willingness": 8.0,           # growth-oriented, long horizon
    },

    # ── 3. Amit Rathore — New Delhi ───────────────────────────────────────────
    # Senior manager at an MNC, owns a flat in Vasant Vihar, moderate risk.
    {
        "investor_name": "Amit Rathore",
        "age": 39,
        "occupation_type": "private_sector",
        "annual_income": 2_200_000,        # ₹22 LPA
        "annual_expense": 1_320_000,       # ₹1.1L/month (Delhi lifestyle)
        "financial_assets": 4_000_000,     # diversified portfolio
        "liabilities_excluding_mortgage": 300_000,   # car + personal loan
        "annual_mortgage_payment": 360_000,          # ₹30k/month home EMI
        "properties_owned": 1,
        "risk_willingness": 7.0,
    },

    # ── 4. Deepa Patel — Ahmedabad, Gujarat ───────────────────────────────────
    # Runs a family textile trading business, high income, two properties.
    {
        "investor_name": "Deepa Patel",
        "age": 43,
        "occupation_type": "family_business",
        "annual_income": 2_800_000,        # ₹28 LPA (business profit)
        "annual_expense": 1_400_000,       # business + household spend
        "financial_assets": 6_500_000,     # business surpluses invested
        "liabilities_excluding_mortgage": 500_000,   # business credit line
        "annual_mortgage_payment": 420_000,          # ₹35k/month for 2nd property
        "properties_owned": 2,
        "risk_willingness": 7.5,
    },

    # ── 5. Vikram Joshi — Mumbai, Maharashtra ─────────────────────────────────
    # Insurance broker (commission-based), variable income, two properties.
    {
        "investor_name": "Vikram Joshi",
        "age": 50,
        "occupation_type": "commission_based",
        "annual_income": 1_500_000,        # ₹15 LPA (variable commissions)
        "annual_expense": 1_050_000,       # ₹87.5k/month Mumbai expenses
        "financial_assets": 3_200_000,     # LIC + MF + FD
        "liabilities_excluding_mortgage": 600_000,   # personal + car loan
        "annual_mortgage_payment": 240_000,          # ₹20k/month for 2nd flat
        "properties_owned": 2,
        "risk_willingness": 5.5,           # moderate-conservative
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
        print(f"  Age      : {profile['age']}   Occupation: {profile['occupation_type']}")
        print(f"  Income   : ₹{profile['annual_income']:,.0f}   Willingness: {profile['risk_willingness']}/10")
        print(f"{'─' * 60}")

        if run_llm:
            result = risk_profiling_chain.invoke(inputs)
        else:
            result = compute_all_scores(inputs)
            result["output"]["risk_summary"] = "(LLM summary skipped)"

        calc = result["calculations"]
        out  = result["output"]

        print(f"  Effective Risk Score : {out['effective_risk_score']:.4f}")
        print(f"  Risk Capacity Score  : {calc['risk_capacity_score_clamped']:.4f}")
        print(f"  Willingness-Cap Gap  : {calc['willingness_capacity_gap']:.4f}  (>3: {calc['gap_exceeds_3']})")
        print(f"  Savings Adjustment   : {calc['savings_rate_adjustment']}")
        print(f"  Was Clamped          : {calc['was_clamped']}  ({calc['clamp_direction']})")

        if run_llm and out.get("risk_summary"):
            print(f"\n  Risk Summary:")
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
