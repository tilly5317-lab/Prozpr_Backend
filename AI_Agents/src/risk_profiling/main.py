"""
LangChain LCEL chain for risk profiling.

Usage:
    from risk_profiling.chain import risk_profiling_chain
    from risk_profiling.models import RiskProfileInput

    inputs = RiskProfileInput(age=35, occupation_type="private_sector", ...)
    result = risk_profiling_chain.invoke(inputs.model_dump())
    # result is a dict matching the JSON schema in risk_profile.md
"""

from functools import cache
from typing import Any, Dict

from common import format_inr_indian
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableLambda

from .prompts import RiskProfileSummary, summary_prompt
from .scoring import compute_all_scores

_SUMMARY_MODEL = "claude-haiku-4-5-20251001"


@cache
def _get_summary_chain(api_key: str | None = None):
    """Build (and cache, per key) the summary chain.

    ``api_key=None`` falls back to the ambient ``ANTHROPIC_API_KEY`` at
    construction time. Callers wanting per-module spend attribution pass their
    key explicitly (see ``run_risk_profiling``) — never via process-global env
    mutation, which both raced under async concurrency and was ignored after
    the first call anyway (the cached chain kept whatever key it was built with).
    """
    llm = ChatAnthropic(
        model=_SUMMARY_MODEL, max_tokens=400, api_key=api_key, temperature=0
    )
    return summary_prompt | llm.with_structured_output(RiskProfileSummary)


def _generate_summary(
    data: Dict[str, Any], api_key: str | None = None
) -> Dict[str, Any]:
    calc = data["calculations"]
    inp = data["inputs"]

    # Pre-format edge-case-prone numerics so the LLM never has to interpret
    # sentinel values (999.0 = "undefined", None = "no income data").
    sr = calc.get("savings_rate")
    savings_rate_pct = "N/A" if sr is None else f"{round(sr * 100)}%"

    cov = calc["expense_coverage_ratio"]
    coverage_str = "N/A (no financial assets)" if cov >= 999.0 else f"{cov:.1f}x"

    dbt = calc["current_debt_percent"]
    debt_str = "N/A (no financial assets)" if dbt >= 999.0 else f"{dbt:.0f}%"

    result = _get_summary_chain(api_key).invoke(
        {
            "age": inp["age"],
            "effective_risk_score": data["output"]["effective_risk_score"],
            "risk_profile_category": calc["risk_profile_category"],
            "risk_capacity_score": calc["risk_capacity_score_clamped"],
            "risk_willingness": inp["risk_willingness"],
            "osi": calc["osi"],
            "osi_category": calc["osi_category"],
            "gap_exceeds_3": calc["gap_exceeds_3"],
            "savings_rate_pct": savings_rate_pct,
            "savings_rate_adjustment": calc["savings_rate_adjustment"],
            "net_financial_assets_indian": format_inr_indian(
                calc["net_financial_assets"]
            )
            or "N/A",
            "expense_coverage": coverage_str,
            "current_debt_percent": debt_str,
            "properties_owned": inp["properties_owned"],
        }
    )
    data["output"]["risk_summary"] = result.summary
    return data


risk_profiling_chain = RunnableLambda(compute_all_scores) | RunnableLambda(
    _generate_summary
)


def run_risk_profiling(
    payload: Dict[str, Any], api_key: str | None = None
) -> Dict[str, Any]:
    """Score + summarize with an explicitly attributed API key.

    Same behaviour as ``risk_profiling_chain.invoke(payload)`` but threads
    ``api_key`` into the summary LLM instead of relying on ambient env state.
    The app layer calls this; the LCEL chain remains for dev tooling.
    """
    return _generate_summary(compute_all_scores(payload), api_key=api_key)
