"""Schema-drift guards for the preference vocabulary on RebalanceAction."""

from app.domains.rebalancing.services.rebal_engine.chat import (
    _INVALID_OVERRIDE_TEMPLATE,
    RebalanceAction,
)


def test_preference_fields_default_to_none():
    a = RebalanceAction(mode="narrate")
    assert a.preference_asks is None
    assert a.excluded_categories is None and a.category_weights is None
    assert a.named_fund is None


def test_stacked_preference_action_parses():
    a = RebalanceAction(mode="consolidate", target_fund_count=4,
                        category_weights={"mid cap": 30.0},
                        excluded_categories=["elss"])
    assert a.category_weights == {"mid cap": 30.0}


def test_invalid_override_template_mentions_exposure():
    assert "exposure" in _INVALID_OVERRIDE_TEMPLATE.lower()
