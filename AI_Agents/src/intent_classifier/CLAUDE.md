# AI_Agents/src/intent_classifier/ — classify a customer question into one of nine intents

Classifies into `asset_allocation`, `financial_planning`, `stock_advice`, `portfolio_query`, `general_market_query`, `rebalancing`, `additional_investment`, `mutual_fund_query`, or `out_of_scope`. For redirect-eligible intents also returns a canned customer-facing message. Returns a label (plus a `tools_needed` list) only — downstream routing happens outside `src/`.

## Entry / contract
- `classifier.py` exposes `IntentClassifier.aclassify(input)` / `.classify(input)` → `ClassificationResult`; builds the LangChain + Claude Haiku pipeline (structured output + prompt caching). The app layer always uses `aclassify` — native `ainvoke`, so a caller timeout actually cancels the HTTP call; a thread-offloaded sync `classify` would keep running.

## Files
- `classifier.py` — the classifier + history formatting.
- `models.py` — `Intent` enum, `Tool` enum (data the answer stage needs), `ClassificationInput`/`ClassificationResult`, `OutOfScopeSubreason`.
- `prompts.py` — system prompt + canned redirect messages.
- `README.md` — human guide.

## Scope
Routing only. It answers "which service area?" and nothing else — it does not read values out of a message, and the field catalogue must never enter this prompt. Extraction is `response_extractor/`, which runs immediately after; keeping them apart is what lets this call stay small and fully cached, so a market question never pays for a catalogue it cannot use.

Two outputs are not strictly classification and are kept because live code depends on them: `tools_needed` (read by `portfolio_query` to gate market commentary — its guidance lives in the pydantic `Field(description=…)`, so it costs schema tokens, not prompt tokens) and `out_of_scope_message` / `out_of_scope_subreason` (the brain's classifier-only short-circuit requires the message; without it a `stock_advice` turn once fell through to general_chat, which answered it). `is_follow_up` was removed in the 2026-08 merge — nothing read it.

## Gotchas & invariants
- `intent` is constrained to a `Literal` so the Anthropic tool schema enforces the enum at the API level — the model physically cannot emit an unknown intent. Keep `_IntentLiteral` in sync with the `Intent` enum; a drift test (`app/domains/ai_engine/tests/test_intent_classifier_schema.py`) fails loudly otherwise (`classifier.py`).
- `tools_needed` is a SECOND, independent verdict — what data the *answer* stage needs, not what the question is about; it does not affect the intent. A new `Tool` member also needs `_ToolLiteral` (same drift test). Its guidance MUST stay in the pydantic `Field(description=...)`: the same text in `prompts.py` primes the market intent and misroutes benchmark questions (`models.py`, `classifier.py`).
- `temperature=0` is load-bearing — unset applies the API default of 1.0, and 4 of 101 labelled turns changed intent between identical runs. Pinning it took eval spread to 0.0% (`classifier.py`).
- `financial_planning` is ONE intent covering plan inputs, goals and the projection over both. It replaced the `goal_planning` / `profile_update` split, which forced the model to decide whether a sentence was a statement or a question before anything could be read out of it — and "my income is now 32L, am I still on track?" is both (`prompts.py` §2).
- `out_of_scope` is not a fallback: the prompt requires a positive reason. Short/vague/ambiguous resolves from `active_intent` or history instead — that was the most visible chat failure (`prompts.py`).
- Field validators scrub stray closing-tag debris (e.g. `"true</is_follow_up>"`) that Anthropic's tool serializer occasionally leaks into values (`classifier.py` `_scrub_tag_noise`).

## Don't read
- `__pycache__/`.
- `Testing/` — pytest suite.
