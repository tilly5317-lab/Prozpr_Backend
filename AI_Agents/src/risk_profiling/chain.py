"""
LangChain LCEL chain for risk profiling.

Usage:
    from risk_profiling.chain import risk_profiling_chain
    from risk_profiling.models import RiskProfileInput

    inputs = RiskProfileInput(age=35, occupation_type="private_sector", ...)
    result = risk_profiling_chain.invoke(inputs.model_dump())
    # result is a dict matching the JSON schema in risk_profile.md
"""

from typing import Any, Dict

from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from .prompts import summary_prompt
from .scoring import compute_all_scores

_llm = ChatAnthropic(model="claude-haiku-4-5-20251001")
_summary_chain = summary_prompt | _llm | StrOutputParser()


def _generate_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    calc = data["calculations"]
    inp = data["inputs"]
    summary = _summary_chain.invoke({
        "age": inp["age"],
        "effective_risk_score": data["output"]["effective_risk_score"],
        "risk_capacity_score": calc["risk_capacity_score_clamped"],
        "risk_willingness": inp["risk_willingness"],
        "osi": calc["osi"],
        "osi_category": calc["osi_category"],
        "gap_exceeds_3": calc["gap_exceeds_3"],
        "savings_rate_adjustment": calc["savings_rate_adjustment"],
        "was_clamped": calc["was_clamped"],
        "clamp_direction": calc["clamp_direction"],
    })
    data["output"]["risk_summary"] = summary
    return data


risk_profiling_chain = RunnableLambda(compute_all_scores) | RunnableLambda(_generate_summary)
