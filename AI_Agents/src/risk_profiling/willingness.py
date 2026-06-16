"""Customer risk-willingness questionnaire scorer.

Four questions; Q1/Q3/Q4 contribute numeric scores, Q2 (experience) caps
the final value.

  avg  = mean(Q1, Q3, Q4)
  mn   = min(Q1, Q3, Q4)
  lift = mn + 2  if mn < 5  else 10
  risk_willingness = min(avg, lift, Q2_cap)

Any of the four answers may be ``None`` (unanswered):
  - Missing Q1/Q3/Q4 are excluded from avg/min/lift.
  - Missing Q2 drops the cap.
  - If all of Q1/Q3/Q4 are missing, risk_willingness falls back to the
    Q2 cap alone (and is ``None`` if Q2 is also missing).
``missing_questions`` in the result lists which inputs were absent.
"""

from typing import Any, Dict, List, Literal, Optional

InvestmentPreference = Literal[
    "-2/11",
    "-6/18",
    "-13/24",
    "-20/30",
    "-27/37",
]
InvestmentExperience = Literal[
    "novice",
    "basic_understanding",
    "understand",
    "experienced",
]
InvestmentFocus = Literal[
    "guaranteed",
    "stable_reliable",
    "some_variability",
    "moderate_variability",
    "high_returns",
]
DropReaction = Literal[
    "capital_preservation",
    "transfer_to_safe",
    "worried",
    "accept_volatility",
    "buy_dips",
]

Q1_SCORE: Dict[str, float] = {
    "-2/11": 2.0,
    "-6/18": 4.0,
    "-13/24": 6.0,
    "-20/30": 8.0,
    "-27/37": 10.0,
}
Q2_CAP: Dict[str, float] = {
    "novice": 7.0,
    "basic_understanding": 8.0,
    "understand": 9.0,
    "experienced": 10.0,
}
Q3_SCORE: Dict[str, float] = {
    "guaranteed": 2.0,
    "stable_reliable": 4.0,
    "some_variability": 6.0,
    "moderate_variability": 8.0,
    "high_returns": 10.0,
}
Q4_SCORE: Dict[str, float] = {
    "capital_preservation": 2.0,
    "transfer_to_safe": 4.0,
    "worried": 6.0,
    "accept_volatility": 8.0,
    "buy_dips": 10.0,
}


def compute_risk_willingness(
    investment_preference: Optional[InvestmentPreference] = None,
    investment_experience: Optional[InvestmentExperience] = None,
    investment_focus: Optional[InvestmentFocus] = None,
    drop_reaction: Optional[DropReaction] = None,
) -> Dict[str, Any]:
    q1 = Q1_SCORE[investment_preference] if investment_preference is not None else None
    q2_cap = (
        Q2_CAP[investment_experience] if investment_experience is not None else None
    )
    q3 = Q3_SCORE[investment_focus] if investment_focus is not None else None
    q4 = Q4_SCORE[drop_reaction] if drop_reaction is not None else None

    score_qs = [s for s in (q1, q3, q4) if s is not None]

    avg: Optional[float]
    mn: Optional[float]
    lift: Optional[float]
    risk_willingness: Optional[float]

    if score_qs:
        avg = round(sum(score_qs) / len(score_qs), 2)
        mn = min(score_qs)
        lift = mn + 2.0 if mn < 5 else 10.0
        constraints = [avg, lift]
        if q2_cap is not None:
            constraints.append(q2_cap)
        risk_willingness = round(min(constraints), 2)
    else:
        avg = None
        mn = None
        lift = None
        risk_willingness = q2_cap

    missing: List[str] = []
    if investment_preference is None:
        missing.append("investment_preference")
    if investment_experience is None:
        missing.append("investment_experience")
    if investment_focus is None:
        missing.append("investment_focus")
    if drop_reaction is None:
        missing.append("drop_reaction")

    return {
        "q1_investment_preference_score": q1,
        "q2_investment_experience_cap": q2_cap,
        "q3_investment_focus_score": q3,
        "q4_drop_reaction_score": q4,
        "avg_of_q1_q3_q4": avg,
        "min_of_q1_q3_q4": mn,
        "lift_from_min": lift,
        "risk_willingness": risk_willingness,
        "missing_questions": missing,
    }
