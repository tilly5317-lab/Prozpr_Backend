from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.onboarding_chat import _build_write_payload, _deterministic_fallback_extract


def test_build_write_payload_maps_core_fields():
    payload = _build_write_payload(
        [
            {"field": "annual_income", "value": 1800000},
            {"field": "annual_expenses", "value": 900000},
            {"field": "investable_assets", "value": 1200000},
            {"field": "emergency_fund", "value": 300000},
            {"field": "investment_horizon", "value": "10 years"},
            {"field": "selected_goals", "value": ["Retirement", "Education"]},
            {"field": "risk_level_label", "value": "moderate"},
            {"field": "drop_reaction", "value": "Hold and rebalance"},
        ]
    )

    assert payload["onboarding_profile"]["annual_income_min"] == 1800000
    assert payload["onboarding_profile"]["annual_expense_max"] == 900000
    assert payload["onboarding_profile"]["investment_horizon"] == "10 years"
    assert payload["investment_profile"]["investable_assets"] == 1200000
    assert payload["investment_profile"]["emergency_fund"] == 300000
    assert payload["risk_profile"]["risk_level"] == 2
    assert payload["risk_profile"]["drop_reaction"] == "Hold and rebalance"


def test_deterministic_fallback_extract_income():
    out = _deterministic_fallback_extract("My annual income is INR 2400000 currently.", None)
    assert out["phase"] == "collecting"
    assert out["extracted_values"]
    assert out["extracted_values"][0]["field"] == "annual_income"


def test_deterministic_fallback_with_lpa():
    out = _deterministic_fallback_extract("income is 18LPA", None)
    vals = out["extracted_values"]
    assert any(v["field"] == "annual_income" for v in vals)
    income_val = next(v for v in vals if v["field"] == "annual_income")
    assert income_val["value"] == 1_800_000


def test_deterministic_fallback_accumulates():
    prior = [{"field": "annual_income", "value": 1800000, "confidence": 0.9}]
    out = _deterministic_fallback_extract("my expense is 16LPA", prior)
    fields = [v["field"] for v in out["extracted_values"]]
    assert "annual_income" in fields
    assert "annual_expenses" in fields


