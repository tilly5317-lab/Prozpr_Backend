# Intent Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `src/router/` — a dispatch-dict router that takes `IntentClassifier` output and routes `general_market_query` to `market_commentary`, with stub responses for all other intents.

**Architecture:** A `Router` class holds a `dict[Intent, Callable[[str, ClassificationResult], str]]`. `Router.route(result, question)` does a single dict lookup, calls the handler, and wraps the result in a `RouterResponse`. The market commentary handler reads a cached `.md` file from a fixed path on disk; all other handlers return stub strings or the classifier's existing canned messages.

**Tech Stack:** Python 3.11+, Pydantic v2, LangChain (via existing `market_commentary` module), pytest + `unittest.mock`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `src/router/__init__.py` | Create | Public exports: `Router`, `RouterResponse` |
| `src/router/models.py` | Create | `RouterResponse` Pydantic model |
| `src/router/router.py` | Create | `Router` class with dispatch dict |
| `src/router/tests/__init__.py` | Create | Makes tests a package |
| `src/router/tests/test_router.py` | Create | Unit tests (mocking `answer_question`) |
| `src/router/dev_run.py` | Create | Manual smoke-test script |

All paths are relative to `src/goal_based_allocation/` — the working directory. Wait: the `src/` folder is at `AI_Agents/src/`. All paths below are relative to `AI_Agents/src/`.

---

## Task 1: `models.py` — RouterResponse

**Files:**
- Create: `src/router/models.py`
- Create: `src/router/__init__.py`
- Create: `src/router/tests/__init__.py`
- Create: `src/router/tests/test_router.py` (stub — just an import check)

- [ ] **Step 1: Create `src/router/models.py`**

```python
from pydantic import BaseModel
from src.intent_classifier.models import Intent


class RouterResponse(BaseModel):
    intent: Intent
    answer: str
    module_used: str   # e.g. "market_commentary" or "stub"
    confidence: float
    reasoning: str
```

- [ ] **Step 2: Create `src/router/__init__.py`** (empty for now — filled in Task 3)

```python
```

- [ ] **Step 3: Create `src/router/tests/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Write the failing import test**

Create `src/router/tests/test_router.py`:

```python
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
```

- [ ] **Step 5: Run test from `AI_Agents/` root**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/ailax/project/backend/AI_Agents
python -m pytest src/router/tests/test_router.py::test_router_response_model -v
```

Expected: **PASS**

- [ ] **Step 6: Commit**

```bash
git add src/router/models.py src/router/__init__.py src/router/tests/__init__.py src/router/tests/test_router.py
git commit -m "feat(router): add RouterResponse model and test"
```

---

## Task 2: `router.py` — Router class

**Files:**
- Create: `src/router/router.py`
- Modify: `src/router/tests/test_router.py` (add Router tests)

### Step-by-step

- [ ] **Step 1: Write the failing tests for Router**

Append to `src/router/tests/test_router.py`:

```python
from unittest.mock import patch
from src.router.router import Router
from src.intent_classifier.models import (
    ClassificationResult,
    Intent,
)


def _make_result(intent: Intent, out_of_scope_message: str | None = None) -> ClassificationResult:
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
        response = router.route(result, "What is the Nifty PE?")
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
        response = router.route(result, "What is the Nifty PE?")
    assert response.module_used == "market_commentary"
    assert "not yet available" in response.answer


# --- stub intents ---

def test_portfolio_query_returns_stub():
    router = Router()
    result = _make_result(Intent.PORTFOLIO_QUERY)
    response = router.route(result, "What is my XIRR?")
    assert response.module_used == "stub"
    assert response.intent == Intent.PORTFOLIO_QUERY
    assert len(response.answer) > 0


def test_portfolio_optimisation_returns_stub():
    router = Router()
    result = _make_result(Intent.PORTFOLIO_OPTIMISATION)
    response = router.route(result, "How should I rebalance?")
    assert response.module_used == "stub"
    assert response.intent == Intent.PORTFOLIO_OPTIMISATION


def test_goal_planning_returns_stub():
    router = Router()
    result = _make_result(Intent.GOAL_PLANNING)
    response = router.route(result, "Help me plan for retirement")
    assert response.module_used == "stub"
    assert response.intent == Intent.GOAL_PLANNING


# --- canned-response intents use classifier's out_of_scope_message ---

def test_stock_advice_returns_out_of_scope_message():
    router = Router()
    result = _make_result(Intent.STOCK_ADVICE, out_of_scope_message="We don't do stocks.")
    response = router.route(result, "Should I buy Infosys?")
    assert response.answer == "We don't do stocks."
    assert response.module_used == "stub"


def test_out_of_scope_returns_out_of_scope_message():
    router = Router()
    result = _make_result(Intent.OUT_OF_SCOPE, out_of_scope_message="Out of scope.")
    response = router.route(result, "What is the weather?")
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
        router.route(result, "test question")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/ailax/project/backend/AI_Agents
python -m pytest src/router/tests/test_router.py -v
```

Expected: **FAIL** — `ModuleNotFoundError: No module named 'src.router.router'`

- [ ] **Step 3: Create `src/router/router.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.intent_classifier.models import ClassificationResult, Intent
from src.market_commentary.chat_qa import answer_question
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


class Router:
    """Routes a ClassificationResult to the appropriate module handler.

    Currently wired:
        general_market_query → market_commentary.chat_qa.answer_question

    All other live-module intents return stub responses.
    stock_advice and out_of_scope return the classifier's canned message.
    """

    def __init__(self) -> None:
        self._dispatch: dict[Intent, Callable[[str, ClassificationResult], str]] = {
            Intent.GENERAL_MARKET_QUERY:    self._handle_market_commentary,
            Intent.PORTFOLIO_QUERY:         self._handle_stub,
            Intent.PORTFOLIO_OPTIMISATION:  self._handle_stub,
            Intent.GOAL_PLANNING:           self._handle_stub,
            Intent.STOCK_ADVICE:            self._handle_canned,
            Intent.OUT_OF_SCOPE:            self._handle_canned,
        }

    def route(self, result: ClassificationResult, question: str) -> RouterResponse:
        """Dispatch to the correct handler and return a RouterResponse.

        Args:
            result: The ClassificationResult from IntentClassifier.classify().
            question: The original customer question string.

        Returns:
            RouterResponse with intent, answer, module_used, confidence, reasoning.

        Raises:
            ValueError: If result.intent has no registered handler (enum drift guard).
        """
        handler = self._dispatch.get(result.intent)
        if handler is None:
            raise ValueError(
                f"No handler registered for intent {result.intent!r}. "
                "Update Router._dispatch to include this intent."
            )
        answer = handler(question, result)
        module_used = (
            "market_commentary"
            if result.intent == Intent.GENERAL_MARKET_QUERY
            else "stub"
        )
        return RouterResponse(
            intent=result.intent,
            answer=answer,
            module_used=module_used,
            confidence=result.confidence,
            reasoning=result.reasoning,
        )

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_market_commentary(
        self, question: str, result: ClassificationResult
    ) -> str:
        try:
            return answer_question(
                user_question=question,
                output_dir=str(_COMMENTARY_DIR),
            )
        except FileNotFoundError:
            return _COMMENTARY_UNAVAILABLE

    def _handle_stub(self, question: str, result: ClassificationResult) -> str:
        return _STUB_COMING_SOON

    def _handle_canned(self, question: str, result: ClassificationResult) -> str:
        return result.out_of_scope_message or _STUB_COMING_SOON
```

- [ ] **Step 4: Run the full test suite**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/ailax/project/backend/AI_Agents
python -m pytest src/router/tests/test_router.py -v
```

Expected: **All PASS**

- [ ] **Step 5: Commit**

```bash
git add src/router/router.py src/router/tests/test_router.py
git commit -m "feat(router): add Router class with dispatch dict and full test coverage"
```

---

## Task 3: `__init__.py` — Public exports

**Files:**
- Modify: `src/router/__init__.py`

- [ ] **Step 1: Update `src/router/__init__.py`**

```python
from .models import RouterResponse
from .router import Router

__all__ = ["Router", "RouterResponse"]
```

- [ ] **Step 2: Verify import works**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/ailax/project/backend/AI_Agents
python -c "from src.router import Router, RouterResponse; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/router/__init__.py
git commit -m "feat(router): export Router and RouterResponse from __init__"
```

---

## Task 4: `dev_run.py` — Manual smoke test

**Files:**
- Create: `src/router/dev_run.py`

- [ ] **Step 1: Create `src/router/dev_run.py`**

```python
"""Manual smoke-test for the Router.

Run from AI_Agents/ root:
    python -m src.router.dev_run

Tests:
  - general_market_query  → market_commentary (or graceful fallback if no cache)
  - portfolio_query       → stub
  - out_of_scope          → canned message
"""

from src.intent_classifier.models import ClassificationResult, Intent
from src.router import Router, RouterResponse


def _make_result(
    intent: Intent,
    out_of_scope_message: str | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        intent=intent,
        confidence=0.92,
        is_follow_up=False,
        reasoning="dev_run synthetic result",
        out_of_scope_message=out_of_scope_message,
    )


def _print_response(label: str, response: RouterResponse) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  intent      : {response.intent.value}")
    print(f"  module_used : {response.module_used}")
    print(f"  confidence  : {response.confidence}")
    print(f"  answer      : {response.answer[:200]}")


def main() -> None:
    router = Router()

    # 1. general_market_query — will hit market_commentary or return graceful fallback
    _print_response(
        "general_market_query",
        router.route(
            _make_result(Intent.GENERAL_MARKET_QUERY),
            "What is the current Nifty 50 PE ratio?",
        ),
    )

    # 2. portfolio_query — stub
    _print_response(
        "portfolio_query (stub)",
        router.route(
            _make_result(Intent.PORTFOLIO_QUERY),
            "What is my portfolio XIRR?",
        ),
    )

    # 3. out_of_scope — canned message from classifier
    _print_response(
        "out_of_scope (canned)",
        router.route(
            _make_result(
                Intent.OUT_OF_SCOPE,
                out_of_scope_message=(
                    "I'm currently set up to help with portfolio optimisation, "
                    "portfolio queries, and general market commentary."
                ),
            ),
            "What is the best cricket team?",
        ),
    )

    # 4. stock_advice — canned message from classifier
    _print_response(
        "stock_advice (canned)",
        router.route(
            _make_result(
                Intent.STOCK_ADVICE,
                out_of_scope_message=(
                    "At Prozper, we don't recommend investing directly in "
                    "individual stocks."
                ),
            ),
            "Should I buy Infosys shares?",
        ),
    )

    print(f"\n{'='*60}")
    print("  dev_run complete")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke test**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/ailax/project/backend/AI_Agents
python -m src.router.dev_run
```

Expected: All 4 sections print without crashing. `general_market_query` either shows a real answer (if `src/market_commentary/output/` has `.md` files) or prints *"Market commentary is not yet available. Please try again later."*

- [ ] **Step 3: Commit**

```bash
git add src/router/dev_run.py
git commit -m "feat(router): add dev_run smoke-test script"
```

---

## Self-Review

**Spec coverage:**
- [x] `src/router/` folder with `__init__.py`, `models.py`, `router.py`, `dev_run.py` — Tasks 1–4
- [x] `RouterResponse` with `intent`, `answer`, `module_used`, `confidence`, `reasoning` — Task 1
- [x] `general_market_query` → `answer_question` with fixed default `output_dir` — Task 2
- [x] Stubs for `portfolio_query`, `portfolio_optimisation`, `goal_planning` — Task 2
- [x] `stock_advice` / `out_of_scope` use `result.out_of_scope_message` — Task 2
- [x] `FileNotFoundError` caught, graceful answer returned — Task 2 (tested + implemented)
- [x] Unknown intent raises `ValueError` — Task 2 (tested + implemented)
- [x] `output_dir` = `Path(__file__).parent.parent / "market_commentary" / "output"` — Task 2

**Placeholder scan:** No TBDs, no "add appropriate error handling" phrases, all code blocks are complete.

**Type consistency:** `ClassificationResult`, `Intent`, `RouterResponse` used consistently across all tasks. Handler signature `(self, question: str, result: ClassificationResult) -> str` is uniform.
