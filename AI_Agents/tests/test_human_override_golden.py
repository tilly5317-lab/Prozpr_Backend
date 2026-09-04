"""Golden pin: with NO preference, both engines are byte-identical to the
pre-S1 baseline. Merge gate for every S1 commit (spec §5).

First run writes the fixture files; every later run compares. Regenerating
the fixtures is ONLY legitimate before any S1 engine change lands.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_practical_input(**overrides):
    from practical_asset_allocation.pipeline import PracticalAllocationInput

    kwargs = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000,
        osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=20_000_000.0,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=20_000_000.0, goals=[],
        mf_corpus=18_000_000.0, non_mf_equity_corpus=1_000_000.0,
        elss_corpus=1_000_000.0,
    )
    kwargs.update(overrides)
    return PracticalAllocationInput(**kwargs)


def _canon(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, indent=1)


def _pin(name: str, payload: str) -> None:
    FIXTURES.mkdir(exist_ok=True)
    path = FIXTURES / name
    if not path.exists():
        path.write_text(payload)
    assert path.read_text() == payload, (
        f"{name}: engine output changed with NO preference set — the "
        "human_override no-op guarantee is broken."
    )


def test_practical_no_pref_is_byte_identical():
    from practical_asset_allocation.pipeline import run_practical_allocation

    out = run_practical_allocation(make_practical_input())
    _pin("golden_practical_no_pref.json", _canon(out))


def test_ideal_no_pref_is_byte_identical():
    from asset_allocation_pydantic.models import AllocationInput
    from asset_allocation_pydantic.pipeline import run_allocation
    from asset_allocation_pydantic.steps import _rationale_llm

    inp = make_practical_input()
    ideal_inp = AllocationInput(
        **{k: getattr(inp, k) for k in AllocationInput.model_fields}
    )
    # rationale_fn=None falls back to the live LLM rationale generator, which
    # is nondeterministic wording (breaks the byte-identical merge gate).
    # no_llm_rationale_fn is the engine's own deterministic drop-in.
    out = run_allocation(ideal_inp, rationale_fn=_rationale_llm.no_llm_rationale_fn)
    _pin("golden_ideal_no_pref.json", _canon(out))
