"""
Goal-based allocation integration smoke test.

5 profiles (shared across AILAX allocation modules):
  1. Rajesh Sharma  — 45, public sector,      risk 7.19, corpus ₹33.5L, tax old 20%
  2. Priya Menon    — 31, private sector,      risk 8.28, corpus ₹13.2L, tax new 30%
  3. Amit Rathore   — 39, private sector,      risk 7.82, corpus ₹37L,   tax new 30%
  4. Deepa Patel    — 43, family business,     risk 8.13, corpus ₹60L,   tax old 35%
  5. Vikram Joshi   — 50, commission_based,    risk 6.61, corpus ₹26L,   tax old 20%

Run:
    cd project/backend/AI_Agents
    python -m src.goal_based_allocation.Testing.dev_run_samples
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_agents_root = Path(__file__).resolve().parents[3]
_backend_root = _agents_root.parent
for env_path in [_agents_root / ".env", _backend_root / ".env"]:
    if env_path.exists():
        load_dotenv(env_path)
        break

from src.goal_based_allocation.main import goal_allocation_chain, run_allocation
from src.goal_based_allocation.models import AllocationInput, Goal

_TESTING_DIR = Path(__file__).resolve().parent

PROFILES: list[tuple[str, AllocationInput]] = [

    ("Rajesh Sharma", AllocationInput(
        # ── From risk_profiling ──────────────────────────────────────────────
        effective_risk_score=7.1906,
        age=45,
        annual_income=1_200_000,
        osi=1.0,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=6.0,
        risk_capacity_score=8.3812,
        net_financial_assets=3_350_000,
        occupation_type="public_sector",
        # ── Gathered by this module ──────────────────────────────────────────
        total_corpus=3_350_000,
        monthly_household_expense=60_000,
        tax_regime="old",
        section_80c_utilized=100_000,
        effective_tax_rate=20.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="Child Education", time_to_goal_months=12,
                 amount_needed=300_000, goal_priority="non_negotiable",
                 investment_goal="education"),
            Goal(goal_name="Home Purchase", time_to_goal_months=60,
                 amount_needed=2_500_000, goal_priority="non_negotiable",
                 investment_goal="home_purchase"),
            Goal(goal_name="Retirement", time_to_goal_months=180,
                 amount_needed=20_000_000, goal_priority="non_negotiable",
                 investment_goal="retirement"),
        ],
    )),

    ("Priya Menon", AllocationInput(
        # ── From risk_profiling ──────────────────────────────────────────────
        effective_risk_score=8.2826,
        age=31,
        annual_income=1_800_000,
        osi=0.8,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=8.0,
        risk_capacity_score=8.5652,
        net_financial_assets=1_320_000,
        occupation_type="private_sector",
        # ── Gathered by this module ──────────────────────────────────────────
        total_corpus=1_320_000,
        monthly_household_expense=75_000,
        tax_regime="new",
        section_80c_utilized=0.0,
        effective_tax_rate=30.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="International Travel", time_to_goal_months=18,
                 amount_needed=300_000, goal_priority="negotiable",
                 investment_goal="other"),
            Goal(goal_name="Retirement", time_to_goal_months=348,
                 amount_needed=30_000_000, goal_priority="non_negotiable",
                 investment_goal="retirement"),
        ],
    )),

    ("Amit Rathore", AllocationInput(
        # ── From risk_profiling ──────────────────────────────────────────────
        effective_risk_score=7.8218,
        age=39,
        annual_income=2_200_000,
        osi=0.8,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=7.0,
        risk_capacity_score=8.6436,
        net_financial_assets=3_700_000,
        occupation_type="private_sector",
        # ── Gathered by this module ──────────────────────────────────────────
        total_corpus=3_700_000,
        monthly_household_expense=110_000,
        tax_regime="new",
        section_80c_utilized=0.0,
        effective_tax_rate=30.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="Home Renovation", time_to_goal_months=18,
                 amount_needed=500_000, goal_priority="negotiable",
                 investment_goal="other"),
            Goal(goal_name="Child Higher Education", time_to_goal_months=60,
                 amount_needed=2_000_000, goal_priority="non_negotiable",
                 investment_goal="education"),
            Goal(goal_name="Retirement", time_to_goal_months=252,
                 amount_needed=25_000_000, goal_priority="non_negotiable",
                 investment_goal="retirement"),
        ],
    )),

    ("Deepa Patel", AllocationInput(
        # ── From risk_profiling ──────────────────────────────────────────────
        effective_risk_score=8.1319,
        age=43,
        annual_income=2_800_000,
        osi=0.6,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=7.5,
        risk_capacity_score=8.7637,
        net_financial_assets=6_000_000,
        occupation_type="family_business",
        # ── Gathered by this module ──────────────────────────────────────────
        total_corpus=6_000_000,
        monthly_household_expense=116_700,
        tax_regime="old",
        section_80c_utilized=150_000,
        effective_tax_rate=35.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="Business Expansion", time_to_goal_months=24,
                 amount_needed=800_000, goal_priority="non_negotiable",
                 investment_goal="other"),
            Goal(goal_name="Child Education", time_to_goal_months=84,
                 amount_needed=3_000_000, goal_priority="non_negotiable",
                 investment_goal="education"),
            Goal(goal_name="Retirement", time_to_goal_months=204,
                 amount_needed=35_000_000, goal_priority="non_negotiable",
                 investment_goal="retirement"),
        ],
    )),

    ("Vikram Joshi", AllocationInput(
        # ── From risk_profiling ──────────────────────────────────────────────
        effective_risk_score=6.6096,
        age=50,
        annual_income=1_500_000,
        osi=0.4,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=5.5,
        risk_capacity_score=7.7192,
        net_financial_assets=2_600_000,
        occupation_type="commission_based",
        # ── Gathered by this module ──────────────────────────────────────────
        total_corpus=2_600_000,
        monthly_household_expense=60_000,
        tax_regime="old",
        section_80c_utilized=100_000,
        effective_tax_rate=20.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="Insurance Premium", time_to_goal_months=6,
                 amount_needed=200_000, goal_priority="non_negotiable",
                 investment_goal="other"),
            Goal(goal_name="Retirement", time_to_goal_months=120,
                 amount_needed=15_000_000, goal_priority="non_negotiable",
                 investment_goal="retirement"),
        ],
    )),
]


def run_customer(name: str, client: AllocationInput) -> dict:
    print(f"\n{'=' * 60}")
    print(f"  Customer: {name}")
    print(f"  Risk Score: {client.effective_risk_score}  |  Age: {client.age}  |  Corpus: ₹{client.total_corpus:,.0f}")
    print(f"  Tax Rate: {client.effective_tax_rate}%  |  Goals: {len(client.goals)}")
    print(f"{'=' * 60}")

    result = goal_allocation_chain.invoke(client.model_dump())

    warnings: list[str] = []

    s7 = result.get("step7_presentation", {})
    grand_total = s7.get("grand_total", 0)
    if abs(grand_total - client.total_corpus) > 100:
        warnings.append(f"grand_total {grand_total} != total_corpus {client.total_corpus}")

    shortfalls = s7.get("shortfall_summary", [])
    if shortfalls:
        for sf in shortfalls:
            bucket = sf.get("bucket", sf.get("step", "unknown"))
            amount = sf.get("shortfall_amount", sf.get("amount", 0))
            warnings.append(f"Shortfall in {bucket}: ₹{amount:,.0f}")

    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}")
    else:
        print(f"  No warnings")

    print(f"\n  Bucket allocations:")
    for bucket in s7.get("bucket_allocations", []):
        print(f"    {bucket['bucket']:<15} allocated: ₹{bucket['allocated_amount']:>12,.0f}")
        for sg, amt in bucket.get("subgroup_amounts", {}).items():
            if amt > 0:
                print(f"      {sg:<30} ₹{amt:>12,.0f}")

    print(f"\n  Grand total: ₹{grand_total:,.0f}")
    return result


def run():
    all_results: dict[str, dict] = {}
    for customer_name, profile in PROFILES:
        all_results[customer_name] = run_customer(customer_name, profile)

    print(f"\n{'=' * 60}")
    print("ALL CUSTOMERS COMPLETED")
    print(f"{'=' * 60}")

    json_path = _TESTING_DIR / "dev_output_samples.json"
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n Saved output to {json_path.name}")


def prod_run():
    """Replicates the production run: uses run_allocation() and prints only step7 output."""
    all_results: dict[str, dict] = {}
    for name, profile in PROFILES:
        print(f"\n{'=' * 60}")
        print(f"  Customer: {name}")
        print(f"{'=' * 60}")
        result = run_allocation(profile)
        print(result.model_dump_json(indent=2))
        all_results[name] = result.model_dump()

    print(f"\n{'=' * 60}")
    print("ALL CUSTOMERS COMPLETED (prod run)")
    print(f"{'=' * 60}")

    json_path = _TESTING_DIR / "prod_output_samples.json"
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n Saved output to {json_path.name}")


if __name__ == "__main__":
    import sys
    if "--prod" in sys.argv:
        prod_run()
    else:
        run()
