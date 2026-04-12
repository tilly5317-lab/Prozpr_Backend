# FIELDS_SEQUENCE, SYSTEM_PROMPT, normalise_answer, get_next_unfilled_field
# ┌──────────────────────────────────────────────────┐
# │  Field Sequence (questionnaire order)           │
# ├──────────────────────────────────────────────────┤
# │  System Prompt (AI instructions)                 │
# ├──────────────────────────────────────────────────┤
# │  State Management (track progress)               │
# ├──────────────────────────────────────────────────┤
# │  Answer Normalization (parse & validate)         │
# └──────────────────────────────────────────────────┘


from __future__ import annotations

from typing import Dict, Optional

from .models import Goal
import re

# =========================
# Conversation field schema
# =========================

# The Question Order Total 38 fields organized in a logical progression
# Design philosophy: Easy → Complex, Personal → Financial → Strategic, Background → Assets → Goals → Risk → Tax → Review

FIELDS_SEQUENCE = [
    "background.client_name",
    "background.occupation",
    "background.family_details",
    "background.wealth_source",
    "background.core_values",
    "goals",
    "annual_income",
    "annual_expenses",
    "one_off_future_inflows",
    "one_off_future_expenses",
    "total_mutual_funds",
    "total_equities",
    "total_debt",
    "total_cash_bank",
    "total_liabilities",
    "properties_value",
    "mortgage_balance",
    "mortgage_emi",
    "return_objective.primary_objectives",
    "return_objective.description",
    "return_objective.required_rate_of_return",
    "return_objective.income_requirement",
    "return_objective.currency",
    "risk_tolerance.overall_risk_tolerance",
    "risk_tolerance.ability_to_take_risk",
    "risk_tolerance.willingness_to_take_risk",
    "risk_tolerance.ability_drivers",
    "risk_tolerance.willingness_drivers",
    "financial_needs.emergency_fund_requirement",
    "financial_needs.liquidity_timeframe",
    "financial_needs.expected_inflows",
    "financial_needs.regular_outflows",
    "financial_needs.planned_large_outflows",
    "tax_profile.current_incometax_rate",
    "tax_profile.current_capitalgainstax_rate",
    "tax_profile.tax_notes",
    "review_process.meeting_frequency",
    "review_process.review_triggers",
    "review_process.update_process",
]

# System prompt
SYSTEM_PROMPT = """
You are a professional wealth-planning assistant helping a human advisor collect all information
needed to populate a ClientSnapshot for an Investment Policy Statement.

You are given:
1) A list of fields that still need to be filled (unfilled_fields).
2) The current conversation so far.

Your tasks:
- Ask ONE concise question at a time, focusing on the next unfilled field.
- Phrase questions in simple, client-friendly language.
- Explicitly tell the client that they may answer 'not applicable' if the question does not apply to them.
- When the client gives an answer that seems ambiguous, you may briefly ask a follow-up, but keep questions short.
- Do NOT output JSON or the field name; only natural language dialogue.
- Avoid giving advice; your role is only to gather information.

Stop asking new questions once there are no unfilled fields left, and just say that you are done collecting information.
"""

# Purpose: Find the next question to ask, Logic: Iterate through FIELDS_SEQUENCE in order >> Check if field exists in conv_state dictionary
# >>Return first missing field >> Return None if all fields are filled

def get_next_unfilled_field(conv_state: Dict) -> Optional[str]:
    for f in FIELDS_SEQUENCE:
        if f not in conv_state:
            return f
    return None

# This is the most complex function - it handles diverse user inputs and converts them to structured data.
# Purpose: Transform natural language answers into typed data

def normalise_answer(field: str, answer: str):    
    #CASE 1  handle not applicable
    if answer.strip().lower() in ["na", "n/a", "not applicable", "none", "no"]:
        return None    
    
    # Case 2: Numeric Fields
    numeric_fields = {
        "annual_income",
        "annual_expenses",
        "total_mutual_funds",
        "total_equities",
        "total_debt",
        "total_cash_bank",
        "total_liabilities",
        "properties_value",
        "mortgage_balance",
        "mortgage_emi",
        "return_objective.required_rate_of_return",
        "return_objective.income_requirement",
        "financial_needs.emergency_fund_requirement",
        "tax_profile.current_incometax_rate",
        "tax_profile.current_capitalgainstax_rate",
    }

    if field in numeric_fields:
        # Remove commas, currency symbols, whitespace
        clean = re.sub(r'[,$₹\s%]', '', answer)     
        # Handle "k" (thousands) and "m" (millions)
        if clean.lower().endswith('k'):
            clean = str(float(clean[:-1]) * 1000)
        elif clean.lower().endswith('m'):
            clean = str(float(clean[:-1]) * 1000000)
        try:
            return float(clean)
        except ValueError:
            return None

    # Case 3: Complex Structured Fields (Goals & Cash Flows)
    if field in ["goals", "one_off_future_inflows", "one_off_future_expenses"]:
        items = []
        parts = [p for p in answer.split(";") if p.strip()]
        for p in parts:
            segs = [s.strip() for s in p.split(",")]
            if field == "goals":
                if len(segs) >= 3:
                    desc = segs[0]
                    try:
                        year = int(segs[1])
                    except ValueError:
                        continue
                    gtype = segs[2].lower()
                    if gtype not in ["growth", "income", "retirement", "expense"]:
                        gtype = "growth"
                    items.append(Goal(description=desc, target_year=year, goal_type=gtype))
            else:
                if len(segs) >= 3:
                    desc = segs[0]
                    try:
                        year = int(segs[1])
                        amount = float(segs[2].replace("%", ""))
                    except ValueError:
                        continue
                    items.append((year, amount, desc))
        return items if items else None

    # Case 4: Text Fields (Default)
    return answer.strip()
