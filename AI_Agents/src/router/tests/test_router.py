from src.router.models import RouterResponse
from src.intent_classifier.models import Intent


def test_router_response_model():
    r = RouterResponse(
        intent=Intent.GENERAL_MARKET_QUERY,
        answer="test answer",
        module_used="market_commentary",
        confidence=0.95,
        reasoning="because markets",
    )
    assert r.intent == Intent.GENERAL_MARKET_QUERY
    assert r.answer == "test answer"
    assert r.module_used == "market_commentary"
    assert r.confidence == 0.95
    assert r.reasoning == "because markets"


from typing import Optional
from unittest.mock import patch
from src.router.router import Router
from src.intent_classifier.models import (
    ClassificationResult,
)


def _make_result(intent: Intent, out_of_scope_message: Optional[str] = None) -> ClassificationResult:
    return ClassificationResult(
        intent=intent,
        confidence=0.9,
        is_follow_up=False,
        reasoning="test reasoning",
        out_of_scope_message=out_of_scope_message,
    )


# --- general_market_query routes to market_commentary ---

def test_general_market_query_calls_answer_question():
    router = Router()
    result = _make_result(Intent.GENERAL_MARKET_QUERY)
    with patch(
        "src.router.router.answer_question",
        return_value="Nifty PE is 22.",
    ) as mock_aq:
        response = router.route("What is the Nifty PE?", result)
    mock_aq.assert_called_once()
    assert response.intent == Intent.GENERAL_MARKET_QUERY
    assert response.answer == "Nifty PE is 22."
    assert response.module_used == "market_commentary"
    assert response.confidence == 0.9
    assert response.reasoning == "test reasoning"


def test_general_market_query_file_not_found_returns_graceful():
    router = Router()
    result = _make_result(Intent.GENERAL_MARKET_QUERY)
    with patch(
        "src.router.router.answer_question",
        side_effect=FileNotFoundError("no files"),
    ):
        response = router.route("What is the Nifty PE?", result)
    assert response.module_used == "market_commentary"
    assert "not yet available" in response.answer


# --- stub intents ---

def test_portfolio_query_returns_stub():
    router = Router()
    result = _make_result(Intent.PORTFOLIO_QUERY)
    response = router.route("What is my XIRR?", result)
    assert response.module_used == "stub"
    assert response.intent == Intent.PORTFOLIO_QUERY
    assert len(response.answer) > 0


def test_portfolio_optimisation_returns_stub():
    router = Router()
    result = _make_result(Intent.PORTFOLIO_OPTIMISATION)
    response = router.route("How should I rebalance?", result)
    assert response.module_used == "stub"
    assert response.intent == Intent.PORTFOLIO_OPTIMISATION


def test_goal_planning_returns_stub():
    router = Router()
    result = _make_result(Intent.GOAL_PLANNING)
    response = router.route("Help me plan for retirement", result)
    assert response.module_used == "stub"
    assert response.intent == Intent.GOAL_PLANNING


# --- canned-response intents use classifier's out_of_scope_message ---

def test_stock_advice_returns_out_of_scope_message():
    router = Router()
    result = _make_result(Intent.STOCK_ADVICE, out_of_scope_message="We don't do stocks.")
    response = router.route("Should I buy Infosys?", result)
    assert response.answer == "We don't do stocks."
    assert response.module_used == "stub"


def test_out_of_scope_returns_out_of_scope_message():
    router = Router()
    result = _make_result(Intent.OUT_OF_SCOPE, out_of_scope_message="Out of scope.")
    response = router.route("What is the weather?", result)
    assert response.answer == "Out of scope."
    assert response.module_used == "stub"


# --- unknown intent raises ValueError ---

def test_unknown_intent_raises():
    import pytest
    router = Router()
    result = _make_result(Intent.GENERAL_MARKET_QUERY)
    # Simulate enum drift by corrupting the dispatch dict after construction
    router._dispatch.clear()
    with pytest.raises(ValueError, match="No handler"):
        router.route("test question", result)


def test_general_market_query_import_error_returns_graceful():
    router = Router()
    result = _make_result(Intent.GENERAL_MARKET_QUERY)
    with patch("src.router.router.answer_question", None), \
         patch("src.router.router._load_answer_question", side_effect=ImportError("no tavily")):
        response = router.route("What is the Nifty PE?", result)
    assert response.module_used == "market_commentary"
    assert "not yet available" in response.answer


def test_canned_falls_back_to_stub_when_no_message():
    router = Router()
    result = _make_result(Intent.OUT_OF_SCOPE, out_of_scope_message=None)
    response = router.route("test", result)
    assert len(response.answer) > 0
    assert response.module_used == "stub"
