"""
LangChain prompt templates for the 7-step goal-based allocation pipeline.
"""

import json
from pathlib import Path
from langchain_core.prompts import ChatPromptTemplate

_REFS = Path(__file__).parent / "references"


def _load(filename: str) -> str:
    content = (_REFS / filename).read_text()
    return content.replace("{", "{{").replace("}", "}}")


def _serialize(state: dict) -> str:
    return json.dumps(state, indent=2, default=str)


# ── Step 1: Emergency Carve-Out ───────────────────────────────────────────────

_STEP1_SYSTEM = _load("emergency.md")
_STEP1_HUMAN = """\
Full client state (inputs only at this stage):

{state_json}

Apply the emergency carve-out rules. Work through Emergency Fund → Negative NFA
in order. All amounts to debt_subgroup. Check shortfall against total_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step1_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP1_SYSTEM),
    ("human", _STEP1_HUMAN),
])


# ── Step 2: Short-Term Goals ──────────────────────────────────────────────────

_STEP2_SYSTEM = _load("short-term-goals.md")
_STEP2_HUMAN = """\
Accumulated state (client inputs + Step 1 output):

{state_json}

Allocate all goals where time_to_goal_months < 24 from step1_emergency.output.remaining_corpus.
Apply the tax-rate instrument selection rule, then check shortfall.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step2_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP2_SYSTEM),
    ("human", _STEP2_HUMAN),
])


# ── Step 3: Medium-Term Goals ─────────────────────────────────────────────────

_STEP3_SYSTEM = _load("medium-term-goals.md")
_STEP3_HUMAN = """\
Accumulated state (client inputs + Steps 1–2 outputs):

{state_json}

Allocate all goals where 24 <= time_to_goal_months <= 60 from step2_short_term.output.remaining_corpus.
Apply risk bucket → equity/debt table per goal, then assign instruments and check shortfall.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step3_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP3_SYSTEM),
    ("human", _STEP3_HUMAN),
])


# ── Step 4: Long-Term Goals ───────────────────────────────────────────────────

_STEP4_SYSTEM = _load("long-term-goals.md")
_STEP4_HUMAN = """\
Accumulated state (client inputs + Steps 1–3 outputs):

{state_json}

Allocate all goals where time_to_goal_months > 60, plus any leftover corpus as
wealth creation, from step3_medium_term.output.remaining_corpus.
Run all 5 phases in order:
  Phase 1 — look up asset-class min/max from effective_risk_score (ceiling to nearest 0.5);
             apply intergenerational transfer override if age > 60 and any goal has
             investment_goal = "intergenerational_transfer"; apply others caveat for score >= 8
  Phase 2 — apply market_commentary proportional scaling to get equities_pct, debt_pct,
             others_pct summing to 100
  Phase 3 — ELSS first-pass if tax_regime = "old" and section_80c_utilized < 150000
  Phase 4 — multi-asset fund allocation using multi_asset_composition: compute
             multi_asset_amount = min(0.5×residual_equity_corpus / (equity_pct/100),
             debt_amount / (debt_pct/100)); decompose into equity/debt/others components;
             derive equity_for_subgroups, debt_for_subgroups, remaining_others_for_gold
  Phase 5 — allocate equity_for_subgroups across 6 subgroups using guardrail table and
             market_commentary scores (value/sector only if commentary score > 7);
             debt_for_subgroups → single tax-based key (debt_subgroup if effective_tax_rate < 20,
             else arbitrage_income); remaining_others_for_gold → gold_commodities

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step4_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP4_SYSTEM),
    ("human", _STEP4_HUMAN),
])


# ── Step 5: Aggregation ───────────────────────────────────────────────────────

_STEP5_SYSTEM = _load("aggregation.md")
_STEP5_HUMAN = """\
Accumulated state (Steps 1–4 outputs):

{state_json}

Consolidate all four bucket subgroup_amounts into a subgroup × investment_type matrix.
Sum to grand_total and verify it equals total_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step5_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP5_SYSTEM),
    ("human", _STEP5_HUMAN),
])


# ── Step 6: Guardrails + Fund Mapping ────────────────────────────────────────

_STEP6_SYSTEM = (
    _load("guardrails.md")
    + "\n\n---\n\n## Mutual Fund Type Reference\n\n"
    + _load("scheme_classification.md")
)
_STEP6_HUMAN = """\
Accumulated state (Steps 1–5 outputs):

{state_json}

1. Validate step4_long_term.output.subgroup_amounts against guardrail rules.
2. Map every subgroup in step5_aggregation.output.rows to sub_category + recommended_fund
   using the Mutual Fund Type Reference in the system prompt.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step6_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP6_SYSTEM),
    ("human", _STEP6_HUMAN),
])


# ── Step 7: Presentation ──────────────────────────────────────────────────────

_STEP7_SYSTEM = _load("presentation.md")
_STEP7_HUMAN = """\
Full pipeline state (client inputs + Steps 1–6 outputs):

{state_json}

Produce the final presentation JSON. Use step6_guardrails.output.fund_mappings for
sub_category and fund recommendations. Write all rationale in plain language.
Verify grand_total equals total_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step7_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP7_SYSTEM),
    ("human", _STEP7_HUMAN),
])


# ── State slimmers ────────────────────────────────────────────────────────────

def _input_fields(state: dict) -> dict:
    """Return all top-level input fields (non-step keys)."""
    return {k: v for k, v in state.items() if not k.startswith("step")}


def _slim_for_step2(state: dict) -> dict:
    return {
        **_input_fields(state),
        "step1_emergency": {"output": state.get("step1_emergency", {}).get("output", {})},
    }


def _slim_for_step3(state: dict) -> dict:
    return {
        **_input_fields(state),
        "step1_emergency": {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
    }


def _slim_for_step4(state: dict) -> dict:
    return {
        **_input_fields(state),
        "step1_emergency":  {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
        "step3_medium_term":{"output": state.get("step3_medium_term", {}).get("output", {})},
    }


def _slim_for_step5(state: dict) -> dict:
    return {
        "total_corpus": state.get("total_corpus"),
        "step1_emergency":  {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
        "step3_medium_term":{"output": state.get("step3_medium_term", {}).get("output", {})},
        "step4_long_term":  {"output": state.get("step4_long_term", {}).get("output", {})},
    }


def _slim_for_step6(state: dict) -> dict:
    return {
        "effective_risk_score": state.get("effective_risk_score"),
        "step4_long_term":  {"output": state.get("step4_long_term", {}).get("output", {})},
        "step5_aggregation":{"output": state.get("step5_aggregation", {}).get("output", {})},
    }


def _slim_for_step7(state: dict) -> dict:
    input_keys = [
        "age", "occupation_type", "effective_risk_score", "total_corpus",
        "goals", "monthly_household_expense", "primary_income_from_portfolio",
        "tax_regime", "section_80c_utilized", "effective_tax_rate",
    ]
    return {
        **{k: state[k] for k in input_keys if k in state},
        "step1_emergency":  {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
        "step3_medium_term":{"output": state.get("step3_medium_term", {}).get("output", {})},
        "step4_long_term":  {"output": state.get("step4_long_term", {}).get("output", {})},
        "step5_aggregation":{"output": state.get("step5_aggregation", {}).get("output", {})},
        "step6_guardrails": {"output": state.get("step6_guardrails", {}).get("output", {})},
    }
