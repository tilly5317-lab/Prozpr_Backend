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
def _get_summary_chain():
    """Build (and cache) the summary chain on first call.

    ``ChatAnthropic`` reads ``ANTHROPIC_API_KEY`` from the environment at
    construction time, so it is built lazily here rather than at import: the
    app layer sets the risk-profiling key around the chain invoke, and the
    first call bakes that key into the cached chain.
    """
    llm = ChatAnthropic(model=_SUMMARY_MODEL, max_tokens=400)
    return summary_prompt | llm.with_structured_output(RiskProfileSummary)


def _generate_summary(data: Dict[str, Any]) -> Dict[str, Any]:
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

    result = _get_summary_chain().invoke(
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
