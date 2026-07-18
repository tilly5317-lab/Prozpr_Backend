"""Customer risk-willingness questionnaire scorer.

Four questions; Q1/Q3/Q4 contribute numeric scores, Q2 (experience) caps
the final value.

  avg  = mean(Q1, Q3, Q4)
  mn   = min(Q1, Q3, Q4)
  lift = mn + 2  if mn < 5  else 10
  risk_willingness = min(avg, lift, Q2_cap)

Inputs are the *frontend-native* values the customer submits, translated to the
internal scoring keys via the reference tables below:
  - Q1 investment preference  -> ``risk_level`` 0-4 (onboarding letter A-E)
  - Q2 investment experience  -> the full option sentence
  - Q3 investment focus        -> the full option sentence
  - Q4 drop reaction           -> the full option sentence
The reference tables are the single place that ties this scorer to the live
questionnaire; the scoring dicts and the maths below are unchanged. The three
text tables MUST stay byte-exact with Prozpr_Frontend/src/pages/CompleteProfile.tsx
(BEHAV_Q*_OPTIONS) — a reworded option is treated as unanswered, not an error.

Any answer may be absent or unrecognised (unanswered):
  - Missing Q1/Q3/Q4 are excluded from avg/min/lift.
  - Missing Q2 drops the cap.
  - If all of Q1/Q3/Q4 are missing, risk_willingness falls back to the
    Q2 cap alone (and is ``None`` if Q2 is also missing).
``missing_questions`` in the result lists which inputs had no usable score.
"""

from typing import Any, Dict, List, Literal, Optional

InvestmentPreference = Literal[
    "-2/10",
    "-5/15",
    "-15/25",
    "-20/30",
    "-30/40",
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
    "-2/10": 2.0,
    "-5/15": 4.0,
    "-15/25": 6.0,
    "-20/30": 8.0,
    "-30/40": 10.0,
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

# --- Reference tables: raw customer answer -> internal scoring key ------------
# Q1: the onboarding preference question stores ``risk_level`` 0-4 (letter A-E).
# The "-x/y" labels are the live worst/best ranges shown to the customer
# (AboutYou.tsx INVESTMENT_PREF_OPTIONS); the score comes from the 0-4 order.
PREFERENCE_RISK_LEVEL_TO_KEY: Dict[int, str] = {
    0: "-2/10",
    1: "-5/15",
    2: "-15/25",
    3: "-20/30",
    4: "-30/40",
}
# Q2/Q3/Q4: the frontend stores the full option sentence verbatim.
# Keys copied byte-exact from CompleteProfile.tsx (BEHAV_Q1/Q2/Q3_OPTIONS).
EXPERIENCE_TEXT_TO_KEY: Dict[str, str] = {
    "I am a novice. I am new to investing and financial markets.": "novice",
    "I have a basic understanding of investing. I understand basic investment concepts like diversification and risks.": "basic_understanding",
    "I am enthusiastic about investing. I understand how markets fluctuate and the pros and cons of different investment classes.": "understand",
    "I am an experienced investor. I have invested in different markets and understand different investment strategies. I have developed my own investment philosophy.": "experienced",
}
FOCUS_TEXT_TO_KEY: Dict[str, str] = {
    "Keep it safe — I'll accept low returns to protect my money": "guaranteed",
    "Mostly steady — small dips are fine for modest growth": "stable_reliable",
    "Balanced — I'll ride moderate ups and downs for moderate growth": "some_variability",
    "Growth-first — I can handle big swings for higher long-term returns": "moderate_variability",
    "Maximise growth — I'm comfortable with large losses while chasing the highest returns": "high_returns",
}
DROP_TEXT_TO_KEY: Dict[str, str] = {
    "Capital preservation is paramount. Cut losses immediately and liquidate all investments.": "capital_preservation",
    "Transfer investments to safer asset classes to prevent further loss.": "transfer_to_safe",
    "Would feel worried but would wait to give your investments a little more time.": "worried",
    "Accept volatility and dips in portfolio value as part of investing. Will keep investments as they are.": "accept_volatility",
    "Buy the dip to bring the average buying price lower. Comfortable sitting with lower portfolio values and waiting for the market to recover in the long term.": "buy_dips",
}


def compute_risk_willingness(
    risk_level: Optional[int] = None,
    investment_experience: Optional[str] = None,
    investment_focus: Optional[str] = None,
    drop_reaction: Optional[str] = None,
) -> Dict[str, Any]:
    # Translate frontend-native answers to internal scoring keys. Anything
    # unrecognised (or None) maps to None and is treated as unanswered.
    pref_key = PREFERENCE_RISK_LEVEL_TO_KEY.get(risk_level) if risk_level is not None else None
    exp_key = EXPERIENCE_TEXT_TO_KEY.get(investment_experience) if investment_experience else None
    focus_key = FOCUS_TEXT_TO_KEY.get(investment_focus) if investment_focus else None
    drop_key = DROP_TEXT_TO_KEY.get(drop_reaction) if drop_reaction else None

    q1 = Q1_SCORE.get(pref_key) if pref_key is not None else None
    q2_cap = Q2_CAP.get(exp_key) if exp_key is not None else None
    q3 = Q3_SCORE.get(focus_key) if focus_key is not None else None
    q4 = Q4_SCORE.get(drop_key) if drop_key is not None else None

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

    # A question is "missing" when it produced no usable score — whether the
    # input was absent or matched no known option.
    missing: List[str] = []
    if q1 is None:
        missing.append("investment_preference")
    if q2_cap is None:
        missing.append("investment_experience")
    if q3 is None:
        missing.append("investment_focus")
    if q4 is None:
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
