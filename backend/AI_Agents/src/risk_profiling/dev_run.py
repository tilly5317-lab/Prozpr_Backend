"""
Risk Profiling — Dev Test Runner
=================================
Runs 100 structured profiles covering all edge cases:
  - Math scoring (compute_all_scores) for all 100 → printed summary table + dev_output.json
  - Full chain with LLM summary for 5 representative profiles → printed in full

Run from src/:  python -m risk_profiling.dev_run
"""

import json
import os
import sys

from dotenv import load_dotenv

# Ensure src/ is on the path when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# project/backend/AI_Agents — pick up ANTHROPIC_API_KEY from .env when present
_AGENTS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

from risk_profiling.main import risk_profiling_chain
from risk_profiling.scoring import compute_all_scores

# ── 100 Test Profiles ─────────────────────────────────────────────────────────

def _p(age, occ, income, expense, assets, liabilities, mortgage, properties, willingness, label=""):
    return {
        "label": label,
        "age": age,
        "occupation_type": occ,
        "annual_income": income,
        "annual_expense": expense,
        "financial_assets": assets,
        "liabilities_excluding_mortgage": liabilities,
        "annual_mortgage_payment": mortgage,
        "properties_owned": properties,
        "risk_willingness": willingness,
    }


PUB = "public_sector"
PVT = "private_sector"
FAM = "family_business"
COM = "commission_based"
FRE = "freelancer_gig"
RET = "retired_homemaker_student"

PROFILES = [
    # ── Age edge cases & interpolation checks ────────────────────────────────
    _p(18,  PVT, 600000, 360000, 500000,  50000, 0,      0, 7.0, "age<20 clamped to 20"),
    _p(20,  PVT, 600000, 360000, 500000,  50000, 0,      0, 7.0, "age=20 anchor"),
    _p(25,  PVT, 600000, 360000, 500000,  50000, 0,      1, 7.0, "age=25 interp → 9.5"),
    _p(30,  PVT, 600000, 360000, 500000,  50000, 0,      1, 7.0, "age=30 anchor → 9"),
    _p(35,  PVT, 600000, 360000, 500000,  50000, 0,      1, 7.0, "age=35 interp → 8.5"),
    _p(40,  PVT, 600000, 360000, 500000,  50000, 0,      1, 7.0, "age=40 anchor → 8"),
    _p(45,  PVT, 600000, 360000, 500000,  50000, 0,      1, 6.0, "age=45 interp → 7.5"),
    _p(50,  PVT, 600000, 360000, 500000,  50000, 0,      1, 6.0, "age=50 anchor → 7"),
    _p(52,  PVT, 600000, 360000, 500000,  50000, 0,      1, 6.0, "age=52 interp → 6.6"),
    _p(55,  PVT, 600000, 360000, 500000,  50000, 0,      1, 5.0, "age=55 anchor → 6"),
    _p(60,  PVT, 600000, 360000, 500000,  50000, 0,      1, 4.0, "age=60 anchor → 5"),
    _p(65,  PVT, 300000, 240000, 800000,  50000, 0,      1, 4.0, "age=65 anchor → 4"),
    _p(70,  RET, 0,      240000, 800000,  50000, 0,      1, 3.0, "age=70 anchor → 3"),
    _p(75,  RET, 0,      240000, 800000,  50000, 0,      1, 3.0, "age=75 interp → 2.5"),
    _p(80,  RET, 0,      180000, 600000,  30000, 0,      1, 2.0, "age=80 anchor → 2"),
    _p(90,  RET, 0,      120000, 300000,  20000, 0,      1, 1.0, "age=90 anchor → 1"),
    _p(95,  RET, 0,      120000, 300000,  20000, 0,      1, 1.0, "age>90 clamped to 90"),

    # ── All 6 occupation types (age=40, otherwise identical) ─────────────────
    _p(40, PUB, 800000, 400000, 1000000, 100000, 120000, 1, 6.0, "OSI: public_sector 1.0"),
    _p(40, PVT, 800000, 400000, 1000000, 100000, 120000, 1, 6.0, "OSI: private_sector 0.8"),
    _p(40, FAM, 800000, 400000, 1000000, 100000, 120000, 1, 6.0, "OSI: family_business 0.6"),
    _p(40, COM, 800000, 400000, 1000000, 100000, 120000, 1, 6.0, "OSI: commission_based 0.4"),
    _p(40, FRE, 800000, 400000, 1000000, 100000, 120000, 1, 6.0, "OSI: freelancer_gig 0.2"),
    _p(40, RET, 0,      400000, 1000000, 100000, 120000, 1, 4.0, "OSI: retired 0.0"),

    # ── Savings rate variation ────────────────────────────────────────────────
    _p(35, PVT, 1000000, 995000,  500000, 50000,  0, 1, 6.0, "savings_rate<1% → equity_reduce"),
    _p(35, PVT, 1000000, 850000,  500000, 50000,  0, 1, 6.0, "savings_rate=15% → none"),
    _p(35, PVT, 1000000, 750000,  500000, 50000,  0, 1, 6.0, "savings_rate=25% → small boost"),
    _p(35, PVT, 1000000, 500000,  500000, 50000,  0, 1, 7.0, "savings_rate=50% → large boost"),
    _p(35, PVT, 1000000, 300000,  500000, 50000,  0, 1, 7.0, "savings_rate=70%+ → max boost 30%"),
    _p(35, PVT, 1000000, 1100000, 500000, 50000,  0, 1, 5.0, "savings_rate negative → equity_reduce"),

    # ── Retired / no income ──────────────────────────────────────────────────
    _p(60, RET, 0, 300000, 2000000, 0,      0, 2, 3.0, "retired wealthy → savings skipped"),
    _p(65, RET, 0, 400000, 500000,  200000, 0, 1, 2.0, "retired modest assets"),
    _p(70, RET, 0, 200000, 100000,  0,      0, 0, 1.0, "retired minimal assets no property"),

    # ── Zero / near-zero financial assets ────────────────────────────────────
    _p(30, PVT, 600000, 400000, 0,       0,      0, 0, 5.0, "zero financial assets"),
    _p(30, PVT, 600000, 400000, 1000,    0,      0, 0, 5.0, "near-zero financial assets"),
    _p(30, PVT, 600000, 400000, 5000000, 0,      0, 0, 8.0, "high assets no property no debt"),

    # ── Negative net financial assets ────────────────────────────────────────
    _p(35, PVT, 900000, 600000, 500000,  800000, 0,      0, 6.0, "net_financial_assets < 0"),
    _p(40, PVT, 800000, 500000, 200000,  500000, 120000, 0, 5.0, "deeply negative net assets"),

    # ── High vs minimal debt ─────────────────────────────────────────────────
    _p(40, PVT, 800000, 400000, 500000,  600000, 0,      1, 6.0, "debt > 100% → current_debt_score=1"),
    _p(40, PVT, 800000, 400000, 2000000, 10000,  0,      1, 7.0, "debt < 6% → current_debt_score=10"),
    _p(40, PVT, 800000, 400000, 1000000, 400000, 120000, 1, 6.0, "debt ~52% → mid score"),

    # ── Properties variation ─────────────────────────────────────────────────
    _p(40, PVT, 800000, 400000, 1000000, 100000, 0, 0, 6.0, "0 properties → score 2"),
    _p(40, PVT, 800000, 400000, 1000000, 100000, 0, 1, 6.0, "1 property → score 8"),
    _p(40, PVT, 800000, 400000, 1000000, 100000, 0, 2, 6.0, "2+ properties → score 10"),
    _p(40, PVT, 800000, 400000, 1000000, 100000, 0, 5, 6.0, "5 properties → score 10"),

    # ── Willingness-Capacity gap ──────────────────────────────────────────────
    _p(30, PVT, 800000, 400000, 2000000, 50000, 0, 1, 10.0, "high willingness young → gap likely small"),
    _p(70, RET, 0,      200000, 500000,  50000, 0, 1, 10.0, "high willingness old → gap > 3"),
    _p(25, PVT, 800000, 600000, 100000,  50000, 0, 0, 1.0,  "low willingness young → gap > 3"),
    _p(40, PVT, 800000, 400000, 1000000, 50000, 0, 1, 5.5,  "gap exactly ~0 — balanced"),
    _p(50, FAM, 700000, 500000, 800000,  100000, 60000, 1, 9.0, "gap > 3: high willingness moderate cap"),
    _p(60, RET, 0,      300000, 1500000, 50000,  0,     2, 1.0, "gap > 3: low willingness high capacity"),

    # ── Clamping: raw score > 10 ─────────────────────────────────────────────
    _p(20, PUB, 2000000, 400000, 5000000, 0,      0, 2, 9.0, "raw > 10 → clamped down"),
    _p(22, PUB, 2000000, 400000, 5000000, 0,      0, 2, 9.0, "nearly clamped down"),

    # ── Clamping: raw score < 1 ──────────────────────────────────────────────
    _p(90, RET, 0,      500000, 50000,   500000, 0, 0, 1.0, "raw < 1 → clamped up"),
    _p(85, RET, 0,      400000, 30000,   400000, 0, 0, 1.0, "nearly clamped up"),

    # ── Expense coverage extremes ─────────────────────────────────────────────
    _p(40, PVT, 800000, 400000, 0,        0, 0, 0, 5.0, "exp_coverage: zero assets → score 1"),
    _p(40, PVT, 800000,      0, 2000000,  0, 0, 1, 7.0, "exp_coverage: zero expenses → score 10"),
    _p(40, PVT, 800000, 400000, 200000,   0, 0, 1, 5.0, "exp_coverage ratio 0.5 → border score 1"),
    _p(40, PVT, 800000, 400000, 5000000,  0, 0, 1, 7.0, "exp_coverage ratio 12.5 → score 10"),
    _p(40, PVT, 800000, 400000, 2400000,  0, 0, 1, 7.0, "exp_coverage ratio 6 → mid score"),

    # ── Combined: aggressive / maximum risk ──────────────────────────────────
    _p(22, PUB, 2000000, 500000, 3000000,  0,      0, 2, 10.0, "max risk: young public rich willingness 10"),
    _p(25, PUB, 1500000, 300000, 2000000,  0,      0, 1, 9.0,  "high risk young public sector"),
    _p(28, PVT, 1200000, 400000, 1500000,  50000,  0, 1, 8.0,  "high risk young private"),

    # ── Combined: conservative / minimum risk ────────────────────────────────
    _p(88, RET, 0,      200000, 50000,   300000, 0, 0, 1.0, "min risk: old retired broke low willingness"),
    _p(80, RET, 0,      300000, 100000,  200000, 0, 0, 1.0, "very conservative retired"),
    _p(75, RET, 0,      400000, 200000,  100000, 0, 0, 2.0, "conservative older retired"),

    # ── Mixed realistic profiles ──────────────────────────────────────────────
    _p(32, PVT, 900000,  540000, 800000,   100000, 120000, 1, 7.0, "typical young professional"),
    _p(38, PVT, 1200000, 720000, 2000000,  200000, 180000, 1, 6.0, "mid-career professional"),
    _p(42, FAM, 1500000, 900000, 3000000,  300000, 240000, 2, 7.0, "business owner mid-age"),
    _p(45, PVT, 1800000, 900000, 5000000,  200000, 300000, 2, 8.0, "senior professional good assets"),
    _p(48, COM, 1200000, 800000, 2000000,  400000, 240000, 1, 6.0, "commission-based mid-career"),
    _p(50, PVT, 1500000, 900000, 4000000,  300000, 240000, 2, 6.0, "approaching retirement good assets"),
    _p(53, PVT, 1200000, 900000, 3000000,  200000, 180000, 1, 5.0, "pre-retirement moderate risk"),
    _p(55, PUB, 1000000, 700000, 5000000,  100000, 0,      2, 5.0, "stable govt pre-retirement"),
    _p(58, PUB, 900000,  700000, 4000000,  100000, 0,      2, 4.0, "near-retirement govt job"),
    _p(62, RET, 0,       350000, 8000000,  0,      0,      2, 4.0, "wealthy retiree"),
    _p(35, FRE, 600000,  500000, 300000,   100000, 0,      0, 6.0, "freelancer moderate savings"),
    _p(29, FRE, 400000,  380000, 100000,   50000,  0,      0, 7.0, "freelancer low savings gig"),
    _p(36, FAM, 2000000, 800000, 5000000,  500000, 360000, 2, 8.0, "family business high income"),
    _p(44, COM, 700000,  600000, 1000000,  300000, 120000, 1, 5.0, "commission-based moderate"),
    _p(27, PVT, 700000,  490000, 400000,   50000,  0,      0, 8.0, "young professional saving well"),
    _p(33, PVT, 800000,  480000, 600000,   80000,  0,      1, 7.0, "young professional with home"),
    _p(41, PVT, 1000000, 700000, 1500000,  200000, 180000, 1, 6.0, "middle class mid-career"),
    _p(46, FAM, 1800000, 1200000, 4000000, 600000, 480000, 2, 7.0, "business owner high expenses"),
    _p(54, PUB, 800000,  600000, 3000000,  100000, 0,      2, 4.0, "stable govt senior"),
    _p(67, RET, 0,       250000, 3000000,  50000,  0,      2, 3.0, "comfortable retiree"),
    _p(72, RET, 0,       200000, 1000000,  30000,  0,      1, 2.0, "modest retiree"),
    _p(77, RET, 0,       180000, 500000,   20000,  0,      1, 2.0, "older retiree"),
    _p(26, PVT, 500000,  490000, 50000,    0,      0,      0, 6.0, "young barely saving"),
    _p(31, PVT, 600000,  420000, 200000,   50000,  0,      0, 7.0, "young saving 30% no property"),
    _p(37, PVT, 900000,  630000, 800000,   100000, 120000, 1, 6.0, "30s professional mortgage"),
    _p(43, FAM, 1200000, 960000, 2000000,  400000, 240000, 1, 6.0, "40s family business"),
    _p(49, PVT, 1400000, 1120000, 3000000, 300000, 240000, 2, 5.0, "late 40s conservative"),
    _p(56, PUB, 900000,  720000, 5000000,  50000,  0,      2, 4.0, "55s govt strong assets"),
    _p(63, RET, 0,       300000, 2000000,  0,      0,      1, 3.0, "early retiree one property"),
    _p(68, RET, 0,       220000, 1500000,  0,      0,      2, 2.0, "late retiree two properties"),

    # ── Additional edge cases to reach 100 ───────────────────────────────────
    _p(40, PVT, 800000, 400000, 1000000, 0,      600000, 1, 6.0, "high annual mortgage payment"),
    _p(40, PVT, 800000, 400000, 1000000, 0,      0,      1, 1.0, "willingness=1 minimum"),
    _p(40, PVT, 800000, 400000, 1000000, 0,      0,      1, 10.0,"willingness=10 maximum"),
    _p(40, PVT, 800000, 400000, 1000000, 0,      0,      1, 5.5, "willingness=5.5 fractional"),
    _p(40, PVT, 800000, 400000, 1000000, 950000, 0,      1, 6.0, "liabilities just under assets"),
]

assert len(PROFILES) == 100, f"Expected 100 profiles, got {len(PROFILES)}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_label(profile: dict) -> dict:
    """Return profile dict without the 'label' key (not a RiskProfileInput field)."""
    return {k: v for k, v in profile.items() if k != "label"}


def _print_table(results: list) -> None:
    header = f"{'#':>3}  {'Age':>4}  {'Occupation':<30}  {'Willingness':>11}  {'EffectiveScore':>14}  {'Gap>3':>5}  {'Clamped':>7}  Label"
    print(header)
    print("-" * len(header))
    for i, (profile, result) in enumerate(zip(PROFILES, results), 1):
        calc = result["calculations"]
        print(
            f"{i:>3}  {profile['age']:>4}  {profile['occupation_type']:<30}  "
            f"{profile['risk_willingness']:>11.1f}  "
            f"{result['output']['effective_risk_score']:>14.4f}  "
            f"{'YES' if calc['gap_exceeds_3'] else 'no':>5}  "
            f"{'YES' if calc['was_clamped'] else 'no':>7}  "
            f"{profile.get('label', '')}"
        )


# ── Spot-check assertions ─────────────────────────────────────────────────────

def _run_spot_checks(results: list) -> None:
    checks = [
        # (profile_index_0based, field_path, expected, tolerance)
        (2,  "calculations.age_based_score", 9.5,  0.001),   # age=25
        (4,  "calculations.age_based_score", 8.5,  0.001),   # age=35
        (8,  "calculations.age_based_score", 6.6,  0.001),   # age=52
        (13, "calculations.age_based_score", 2.5,  0.001),   # age=75
    ]
    print("\n── Spot Checks ──")
    all_pass = True
    for idx, path, expected, tol in checks:
        parts = path.split(".")
        val = results[idx]
        for part in parts:
            val = val[part]
        ok = abs(val - expected) <= tol
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{status}] Profile {idx+1} ({PROFILES[idx].get('label','')}) — {path} = {val} (expected {expected})")
    print(f"\nSpot checks: {'ALL PASSED' if all_pass else 'SOME FAILED'}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_dotenv(os.path.join(_AGENTS_ROOT, ".env"))

    print("=" * 80)
    print("RISK PROFILING — 100-Profile Test Run")
    print("=" * 80)

    # 1. Run math-only scoring for all 100
    print("\nRunning deterministic scoring for all 100 profiles...")
    scoring_results = [compute_all_scores(_strip_label(p)) for p in PROFILES]
    print("Done.\n")

    # 2. Print summary table
    print("── Results Summary ──\n")
    _print_table(scoring_results)

    # 3. Spot checks
    _run_spot_checks(scoring_results)

    # 4. Save all 100 math results to dev_output.json
    output_path = os.path.join(os.path.dirname(__file__), "dev_output.json")
    with open(output_path, "w") as f:
        json.dump(scoring_results, f, indent=2)
    print(f"\nFull scoring results saved to: {output_path}")

    # 5. Full chain (with LLM summary) for 5 representative profiles
    sample_indices = [
        0,   # age=18 clamped — young
        22,  # savings_rate < 1% — equity reduce
        47,  # willingness > 3 gap
        57,  # max risk combined
        63,  # min risk combined
    ]
    print("\n" + "=" * 80)
    print("FULL CHAIN (with LLM summary) — 5 representative profiles")
    print("=" * 80)
    for idx in sample_indices:
        profile = PROFILES[idx]
        label = profile.get("label", f"Profile {idx+1}")
        print(f"\n── Profile {idx+1}: {label} ──")
        result = risk_profiling_chain.invoke(_strip_label(profile))
        print(f"  effective_risk_score : {result['output']['effective_risk_score']}")
        print(f"  gap_exceeds_3        : {result['calculations']['gap_exceeds_3']}")
        print(f"  was_clamped          : {result['calculations']['was_clamped']}")
        print(f"  risk_summary:\n")
        for line in result["output"]["risk_summary"].split("\n"):
            print(f"    {line}")
        print()


if __name__ == "__main__":
    main()
