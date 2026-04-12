# wealth_core/reasoning.py
from __future__ import annotations
from typing import Any, Dict
from .models import ClientSnapshot
from .allocation_reasoning import serialize_client_input

def explain_client_profile(snapshot: ClientSnapshot) -> Dict[str, str]:
    """
    Produces narrative explanations by populating the specific 
    assessment fields defined in the ClientSnapshot model.
    """
    # Use unified serialization to maintain logic consistency
    serialize_client_input(snapshot)
    bg = snapshot.background
    
    # 1. Profile Summary
    profile_summary = (
        f"{bg.client_name}, working as {bg.occupation or 'Professional'}, "
        f"presents a profile based on: {bg.wealth_source or 'accumulated savings'}."
    )

    # 2. Risk vs Return Assessment
    risk_return_assessment = (
        f"With an overall risk tolerance of {snapshot.risk_tolerance.overall_risk_tolerance}, "
        f"the strategy targets the primary objective: {snapshot.return_objective.primary_objectives}. "
        f"The required rate of return is set at {snapshot.return_objective.required_rate_of_return or 0:.2%}."
    )

    # 3. Goals Alignment
    if snapshot.goals:
        goals_text = "; ".join([f"{g.description} ({g.target_year})" for g in snapshot.goals])
        goals_alignment_assessment = f"Strategy is optimized for the following milestones: {goals_text}."
    else:
        goals_alignment_assessment = "No specific goals provided; focusing on general capital preservation."

    # Update the snapshot instance directly (if your workflow supports mutation)
    snapshot.profile_summary = profile_summary
    snapshot.risk_return_assessment = risk_return_assessment
    snapshot.goals_alignment_assessment = goals_alignment_assessment

    return {
        "profile_summary": profile_summary,
        "risk_return_assessment": risk_return_assessment,
        "goals_alignment_assessment": goals_alignment_assessment,
    }

def risk_profile_embedding(snapshot: ClientSnapshot) -> Any:
    """
    Public entry point for other modules to get the client's vector representation.
    We reuse the unified serialization and embedding logic from allocation_reasoning.
    """
    from .allocation_reasoning import get_embedding, serialize_client_input
    
    # Use the EXACT same serialization as the allocation engine 
    # so the 'coordinates' in your vector space are identical.
    text = serialize_client_input(snapshot)
    return get_embedding(text)
