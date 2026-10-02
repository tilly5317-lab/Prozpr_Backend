"""Direct stocks are not an engine input. Fails if the machinery reappears in
runtime code. The practical-run ORM model keeps its legacy columns."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"non_mf_equit|excess_direct_stocks|SELL_DIRECT_STOCKS")
SKIP_DIRS = {"tests", "Testing", "Master_testing", "__pycache__"}
ALLOW = {ROOT / "app/domains/practical_asset_allocation/models/run.py"}


def test_no_runtime_code_mentions_direct_stock_machinery():
    hits = []
    for base in (ROOT / "AI_Agents/src", ROOT / "app"):
        for path in base.rglob("*.py"):
            if path in ALLOW or SKIP_DIRS.intersection(path.relative_to(base).parts):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PATTERN.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_the_practical_input_rejects_removed_stock_fields():
    import pytest
    from pydantic import ValidationError

    from practical_asset_allocation.pipeline import PracticalAllocationInput

    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, total_corpus=10_000_000.0,
        monthly_household_expense=100_000, effective_tax_rate=15.0, goals=[],
    )
    PracticalAllocationInput(**base)
    with pytest.raises(ValidationError):
        PracticalAllocationInput(**base, non_mf_equity_corpus=1_000_000.0)
