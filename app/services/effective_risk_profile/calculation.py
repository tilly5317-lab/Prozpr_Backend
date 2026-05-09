"""Effective risk profile — `calculation.py`.

Thin shim over the AI_Agents ``risk_profiling`` pipeline. ``compute_effective_risk_document``
invokes the full LangChain chain (deterministic scoring + Claude Haiku summary) and returns
the persisted JSON document verbatim.

``EffectiveRiskComputationInput`` and ``risk_willingness_from_risk_level`` are backend-only
conveniences that don't exist upstream.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

_AI_AGENTS_SRC = str((Path(__file__).resolve().parents[3] / "AI_Agents" / "src"))
if _AI_AGENTS_SRC not in sys.path:
    sys.path.insert(0, _AI_AGENTS_SRC)

# NOTE: Do not import ``risk_profiling`` at module scope. That chain pulls in
# ``langchain_anthropic`` (ChatAnthropic) and would crash uvicorn on startup if
# optional AI deps are missing from the active venv. Import lazily inside
# ``compute_effective_risk_document`` only.


OccupationType = Literal[
    "public_sector",
    "private_sector",
    "family_business",
    "commission_based",
    "freelancer_gig",
    "retired_homemaker_student",
]


@dataclass(frozen=True)
class EffectiveRiskComputationInput:
    age: float
    occupation_type: str
    annual_income: float
    annual_expense: float
    financial_assets: float
    liabilities_excluding_mortgage: float
    annual_mortgage_payment: float
    properties_owned: int
    risk_willingness: float


def compute_effective_risk_document(inp: EffectiveRiskComputationInput) -> dict[str, Any]:
    """Run scoring + LLM summary via AI_Agents and return the persisted JSON doc."""
    from risk_profiling import risk_profiling_chain  # noqa: E402
    from risk_profiling.scoring import OSI_MAP  # noqa: E402

    occ = inp.occupation_type if inp.occupation_type in OSI_MAP else "private_sector"
    payload = {
        "age": int(inp.age),
        "occupation_type": occ,
        "annual_income": float(inp.annual_income),
        "annual_expense": float(inp.annual_expense),
        "financial_assets": float(inp.financial_assets),
        "liabilities_excluding_mortgage": float(inp.liabilities_excluding_mortgage),
        "annual_mortgage_payment": float(inp.annual_mortgage_payment),
        "properties_owned": int(inp.properties_owned),
        "risk_willingness": max(1.0, min(10.0, float(inp.risk_willingness))),
    }
    return risk_profiling_chain.invoke(payload)


def risk_willingness_from_risk_level(risk_level: Optional[int]) -> Optional[float]:
    """Map legacy 0–4 risk_level to 1–10 willingness when explicit willingness is not stored."""
    if risk_level is None:
        return None
    if not (0 <= risk_level <= 4):
        return None
    return 1.0 + risk_level * (9.0 / 4.0)
