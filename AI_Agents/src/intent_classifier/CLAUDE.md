# AI_Agents/src/intent_classifier

Classifies a customer's financial question into one of six intents — `asset_allocation`, `goal_planning`, `stock_advice`, `portfolio_query`, `general_market_query`, `out_of_scope` — using Claude Haiku with structured output and Anthropic prompt caching. For redirect-eligible intents, also returns a canned customer-facing message.

## Files

- `classifier.py` — `IntentClassifier`; builds the LangChain + Claude Haiku pipeline and formats conversation history.
- `models.py` — `Intent` enum, `ConversationMessage`, `ClassificationInput`, `ClassificationResult`.
- `prompts.py` — system prompt and canned redirect messages.

## Data contract

- Input: `ClassificationInput`
- Output: `ClassificationResult`

## Depends on

- `langchain-anthropic`, Claude Haiku
- `python-dotenv`; `ANTHROPIC_API_KEY` env var
- Does not import any other `src/` modules; routing downstream is handled by the caller.

## Don't read

- `__pycache__/`

## Refresh

If stale, run `/refresh-context` from this folder.
