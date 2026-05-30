"""Customer risk-willingness questionnaire scorer.

Four questions; Q1/Q3/Q4 contribute numeric scores, Q2 (experience) caps
the final value.

  avg  = mean(Q1, Q3, Q4)
  mn   = min(Q1, Q3, Q4)
  lift = mn + 2  if mn < 5  else 10
  risk_willingness = min(avg, lift, Q2_cap)
"""

from typing import Any, Dict, Literal

InvestmentPreference = Literal[
    "-2/11", "-6/18", "-13/24", "-20/30", "-27/37",
]
InvestmentExperience = Literal[
    "novice", "basic_understanding", "understand", "experienced",
]
InvestmentFocus = Literal[
    "guaranteed", "stable_reliable", "some_variability",
    "moderate_variability", "high_returns",
]
DropReaction = Literal[
    "capital_preservation", "transfer_to_safe", "worried",
    "accept_volatility", "buy_dips",
]

Q1_SCORE: Dict[str, float] = {
    "-2/11": 2.0, "-6/18": 4.0, "-13/24": 6.0, "-20/30": 8.0, "-27/37": 10.0,
}
Q2_CAP: Dict[str, float] = {
    "novice": 7.0, "basic_understanding": 8.0, "understand": 9.0, "experienced": 10.0,
}
Q3_SCORE: Dict[str, float] = {
    "guaranteed": 2.0, "stable_reliable": 4.0, "some_variability": 6.0,
    "moderate_variability": 8.0, "high_returns": 10.0,
}
Q4_SCORE: Dict[str, float] = {
    "capital_preservation": 2.0, "transfer_to_safe": 4.0, "worried": 6.0,
    "accept_volatility": 8.0, "buy_dips": 10.0,
}


def compute_risk_willingness(
    investment_preference: InvestmentPreference,
    investment_experience: InvestmentExperience,
    investment_focus: InvestmentFocus,
    drop_reaction: DropReaction,
) -> Dict[str, Any]:
    q1 = Q1_SCORE[investment_preference]
    q2_cap = Q2_CAP[investment_experience]
    q3 = Q3_SCORE[investment_focus]
    q4 = Q4_SCORE[drop_reaction]

    avg = round((q1 + q3 + q4) / 3, 2)
    mn = min(q1, q3, q4)
    lift = mn + 2.0 if mn < 5 else 10.0
    risk_willingness = round(min(avg, lift, q2_cap), 2)

    return {
        "q1_investment_preference_score": q1,
        "q2_investment_experience_cap": q2_cap,
        "q3_investment_focus_score": q3,
        "q4_drop_reaction_score": q4,
        "avg_of_q1_q3_q4": avg,
        "min_of_q1_q3_q4": mn,
        "lift_from_min": lift,
        "risk_willingness": risk_willingness,
    }
