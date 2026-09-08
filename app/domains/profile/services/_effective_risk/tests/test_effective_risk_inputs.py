"""Tests for ``derive_risk_willingness`` — the one production wiring point that
feeds the 4-question willingness model into effective-risk scoring.

``derive_risk_willingness`` only reads attributes off the RiskProfile row, so a
lightweight stand-in with the same fields keeps this unit test decoupled from
the ORM mapper graph (instantiating the real model would require every related
model to be registered first).
"""

from types import SimpleNamespace

from app.domains.profile.services._effective_risk.inputs import (
    derive_risk_willingness,
)

# Byte-exact frontend option sentences (CompleteProfile.tsx BEHAV_Q*_OPTIONS).
EXP_BASIC = "I have a basic understanding of investing. I understand basic investment concepts like diversification and risks."
FOCUS_STEADY = "Mostly steady — small dips are fine for modest growth"
DROP_BUY = "Buy the dip to bring the average buying price lower. Comfortable sitting with lower portfolio values and waiting for the market to recover in the long term."


def _risk(**kw):
    """A RiskProfile-shaped row; every willingness field defaults to None."""
    fields = {
        "risk_willingness": None,
        "risk_level": None,
        "investment_experience": None,
        "investment_focus": None,
        "drop_reaction": None,
    }
    fields.update(kw)
    return SimpleNamespace(**fields)


def test_onboarding_only_uses_model_q1_score():
    # risk_level answered, behavioural questions not -> Q1-only willingness.
    assert derive_risk_willingness(_risk(risk_level=2)) == 6.0


def test_full_four_answers_blend():
    # The screenshot example: rl3(8) + basic(cap8) + steady(4) + buy(10) -> 6.0
    risk = _risk(
        risk_level=3,
        investment_experience=EXP_BASIC,
        investment_focus=FOCUS_STEADY,
        drop_reaction=DROP_BUY,
    )
    assert derive_risk_willingness(risk) == 6.0


def test_explicit_column_override_wins():
    # An explicitly stored risk_willingness takes precedence over the model.
    assert derive_risk_willingness(_risk(risk_level=2, risk_willingness=7.3)) == 7.3


def test_unrecognised_legacy_answer_is_ignored():
    # A stored value that matches no current option is treated as unanswered,
    # so willingness falls back to whatever *is* answered (here Q1 from rl2).
    assert derive_risk_willingness(_risk(risk_level=2, drop_reaction="Wait it out")) == 6.0


def test_all_missing_returns_neutral_5():
    assert derive_risk_willingness(_risk()) == 5.0


def test_none_risk_returns_neutral_5():
    assert derive_risk_willingness(None) == 5.0
