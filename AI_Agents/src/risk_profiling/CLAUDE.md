# AI_Agents/src/risk_profiling

Computes a client's risk profile: deterministic Python scoring (risk capacity, OSI, savings-rate adjustment, clamping, effective risk score) followed by a Claude Haiku summary paragraph. Produces the `effective_risk_score` and supporting fields consumed downstream by allocation modules via their `AllocationInput`.

## Files

- `main.py` — exposes `risk_profiling_chain` (LCEL: scoring → LLM summary).
- `models.py` — `RiskProfileInput`, `RiskProfileOutput`.
- `prompts.py` — `summary_prompt` template.
- `scoring.py` — pure-Python scoring logic.
- `dev_run.py` — developer smoke-test runner.
- `customer_test_data.py` — canned customer profiles for exercising the chain.

## Data contract

- Input: `RiskProfileInput`
- Output: `RiskProfileOutput`

## Depends on

- `langchain-anthropic`, Claude Haiku
- `python-dotenv`; `ANTHROPIC_API_KEY` env var
- Does not import any other `src/` modules.

## Don't read

- `__pycache__/`
- `customer_test_output.json`, `customer_test_output.csv` — captured run artifacts, not schemas
