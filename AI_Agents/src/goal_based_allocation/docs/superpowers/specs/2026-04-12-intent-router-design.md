# Intent Router — Design Spec

**Date:** 2026-04-12  
**Status:** Approved

---

## Overview

A new `src/router/` module that accepts the output of the `IntentClassifier` and dispatches to the appropriate downstream module. For the initial implementation, only `general_market_query` is wired to a live module (`market_commentary`); all other live-module intents are stubs returning a "coming soon" message.

---

## Files & Structure

```
src/router/
├── __init__.py          # exports Router, RouterResponse
├── models.py            # RouterResponse Pydantic model
├── router.py            # Router class with dispatch dict
└── dev_run.py           # manual smoke-test script
```

---

## Models

### `RouterResponse`

```python
class RouterResponse(BaseModel):
    intent: Intent
    answer: str
    module_used: str      # e.g. "market_commentary" or "stub"
    confidence: float     # passed through from ClassificationResult
    reasoning: str        # passed through from ClassificationResult
```

---

## Architecture

`Router` is a plain class. On construction it builds a `dict[Intent, Callable[[str], str]]` mapping each intent to a handler. `Router.route(result, question)` does a single dict lookup and calls the handler, returning a `RouterResponse`.

### Dispatch map

| Intent | Handler | `module_used` |
|---|---|---|
| `general_market_query` | `market_commentary.chat_qa.answer_question` | `"market_commentary"` |
| `portfolio_query` | stub | `"stub"` |
| `portfolio_optimisation` | stub | `"stub"` |
| `goal_planning` | stub | `"stub"` |
| `stock_advice` | returns `result.out_of_scope_message` | `"stub"` |
| `out_of_scope` | returns `result.out_of_scope_message` | `"stub"` |

### Commentary directory

The market commentary handler reads cached `.md` files from a fixed default path:

```
Path(__file__).parent.parent / "market_commentary" / "output"
```

This is `src/market_commentary/output/` relative to the router module — no configuration required.

---

## Data Flow

```
ClassificationResult + customer_question
        ↓
    Router.route(result, question)
        ↓  dict lookup on result.intent
        ↓
    handler(question) → str
        ↓
    RouterResponse(intent, answer, module_used, confidence, reasoning)
```

---

## Error Handling

- **`FileNotFoundError`** from `answer_question` (no cached commentary on disk): caught, returns graceful answer string — *"Market commentary is not yet available. Please try again later."* — with `module_used="market_commentary"`.
- **Unknown intent** (enum drift): `route()` raises `ValueError`. This should never happen if `Intent` enum is exhaustive but acts as a safety guard.

---

## Testing

A `dev_run.py` script manually exercises the router with a few sample `ClassificationInput` objects covering `general_market_query` and stub intents, printing the full `RouterResponse` for each. Consistent with the pattern used in `portfolio_query/dev_run.py` and `risk_profiling/dev_run.py`.

---

## Extensibility

To wire up a new intent when its module is ready:
1. Replace the stub entry in the dispatch dict in `Router.__init__` with the real handler callable.
2. Update `module_used` string.
3. No other changes needed.
