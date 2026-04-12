from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.intent_classifier.models import ClassificationResult, Intent
from src.router.models import RouterResponse

_COMMENTARY_DIR = (
    Path(__file__).parent.parent / "market_commentary" / "output"
)

_STUB_COMING_SOON = (
    "This feature is coming soon to Prozper! Stay tuned for updates."
)

_COMMENTARY_UNAVAILABLE = (
    "Market commentary is not yet available. Please try again later."
)


@dataclass(frozen=True)
class _Route:
    handler: Callable[[str, ClassificationResult], str]
    module: str


def _load_answer_question():
    """Import answer_question directly from the chat_qa submodule.

    Using importlib to load the submodule by file path avoids triggering
    src/market_commentary/__init__.py, which pulls in heavy optional
    dependencies (tavily, etc.) that are not needed for routing or testing.
    """
    mod_name = "src.market_commentary.chat_qa"
    if mod_name in sys.modules:
        return sys.modules[mod_name].answer_question

    spec = importlib.util.spec_from_file_location(
        mod_name,
        Path(__file__).parent.parent / "market_commentary" / "chat_qa.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module.answer_question


# Module-level name so that unittest.mock.patch("src.router.router.answer_question")
# can replace it during tests. Populated lazily on first call to avoid importing
# heavy optional dependencies at import time.
answer_question = None  # replaced by _handle_market_commentary on first use


class Router:
    """Routes a ClassificationResult to the appropriate module handler.

    Currently wired:
        general_market_query → market_commentary.chat_qa.answer_question

    All other live-module intents return stub responses.
    stock_advice and out_of_scope return the classifier's canned message.
    """

    def __init__(self) -> None:
        self._dispatch: dict[Intent, _Route] = {
            Intent.GENERAL_MARKET_QUERY:   _Route(self._handle_market_commentary, "market_commentary"),
            Intent.PORTFOLIO_QUERY:        _Route(self._handle_stub, "stub"),
            Intent.PORTFOLIO_OPTIMISATION: _Route(self._handle_stub, "stub"),
            Intent.GOAL_PLANNING:          _Route(self._handle_stub, "stub"),
            Intent.STOCK_ADVICE:           _Route(self._handle_canned, "stub"),
            Intent.OUT_OF_SCOPE:           _Route(self._handle_canned, "stub"),
        }

    def route(self, question: str, result: ClassificationResult) -> RouterResponse:
        """Dispatch to the correct handler and return a RouterResponse.

        Args:
            question: The original customer question string.
            result: The ClassificationResult from IntentClassifier.classify().

        Returns:
            RouterResponse with intent, answer, module_used, confidence, reasoning.

        Raises:
            ValueError: If result.intent has no registered handler (enum drift guard).
        """
        route = self._dispatch.get(result.intent)
        if route is None:
            raise ValueError(
                f"No handler registered for intent {result.intent!r}. "
                "Update Router._dispatch to include this intent."
            )
        answer = route.handler(question, result)
        return RouterResponse(
            intent=result.intent,
            answer=answer,
            module_used=route.module,
            confidence=result.confidence,
            reasoning=result.reasoning,
        )

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_market_commentary(
        self, question: str, result: ClassificationResult
    ) -> str:
        import src.router.router as _self_module  # re-import self to pick up mock patches in tests

        fn = _self_module.answer_question
        if fn is None:
            try:
                fn = _load_answer_question()
            except ImportError:
                # Circular import or missing market_commentary dependencies
                return _COMMENTARY_UNAVAILABLE
            _self_module.answer_question = fn

        try:
            return fn(
                user_question=question,
                output_dir=str(_COMMENTARY_DIR),
            )
        except FileNotFoundError:
            return _COMMENTARY_UNAVAILABLE

    def _handle_stub(self, _question: str, _result: ClassificationResult) -> str:
        return _STUB_COMING_SOON

    def _handle_canned(self, question: str, result: ClassificationResult) -> str:
        return result.out_of_scope_message or _STUB_COMING_SOON
