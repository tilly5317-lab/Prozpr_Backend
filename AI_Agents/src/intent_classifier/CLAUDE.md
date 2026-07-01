# AI_Agents/src/intent_classifier/ — classify a customer question into one of eight intents

Classifies into `asset_allocation`, `goal_planning`, `stock_advice`, `portfolio_query`, `general_market_query`, `rebalancing`, `additional_investment`, or `out_of_scope`. For redirect-eligible intents also returns a canned customer-facing message. Returns a label only — downstream routing happens outside `src/`.

## Entry / contract
- `classifier.py` exposes `IntentClassifier.classify(input)` → `ClassificationResult`; builds the LangChain + Claude Haiku pipeline (structured output + prompt caching).

## Files
- `classifier.py` — the classifier + history formatting.
- `models.py` — `Intent` enum, `ClassificationInput`/`ClassificationResult`, `OutOfScopeSubreason`.
- `prompts.py` — system prompt + canned redirect messages.
- `README.md` — human guide.

## Gotchas & invariants
- `intent` is constrained to a `Literal` so the Anthropic tool schema enforces the enum at the API level — the model physically cannot emit an unknown intent. Keep `_IntentLiteral` in sync with the `Intent` enum; a drift test (`app/domains/ai_engine/tests/test_intent_classifier_schema.py`) fails loudly otherwise (`classifier.py`).
- Field validators scrub stray closing-tag debris (e.g. `"true</is_follow_up>"`) that Anthropic's tool serializer occasionally leaks into values (`classifier.py` `_scrub_tag_noise`).

## Don't read
- `__pycache__/`.
- `Testing/` — pytest suite.
