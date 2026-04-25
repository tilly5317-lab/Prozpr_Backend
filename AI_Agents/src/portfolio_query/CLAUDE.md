# AI_Agents/src/portfolio_query

Handles the `portfolio_query` intent: answers client questions about their own portfolio using three context sources — the fund house's market commentary, the client profile, and the client's current portfolio (asset-class, sub-category, and per-fund detail). Applies scope guardrails and returns either a factual in-scope answer or a canned redirect.

Self-contained module — does not import from any other `AI_Agents/src/` package.

## Files

- `orchestrator.py` — `PortfolioQueryOrchestrator`; entry point. Also contains `_load_market_commentary()` reading `AI_Agents/Reference_docs/market_commentary_latest.md`.
- `models.py` — pydantic models: `ConversationTurn`, `PortfolioQueryResponse`, `ClientContext`, `PortfolioContext`, `Holding`, `AllocationRow`, `SubCategoryAllocationRow`.
- `llm_client.py` — `LLMClient`; thin Anthropic SDK wrapper with prompt caching.
- `skill_executor.py` — `SkillExecutor`; loads markdown skill files (YAML front matter + system/user templates) and runs them through the LLM.
- `dev_run.py` — developer smoke-test covering in-scope multi-turn and guardrail-trigger scenarios.
- `portfolio_query.md` — prompt-adjacent skill source (system + user prompts) loaded at runtime; not documentation.
- `guardrails.md` — prompt-adjacent scope-rule source embedded into the skill's system prompt; not documentation.

## Data contract

- Input: `ClientContext`, `PortfolioContext`, `question: str`, optional `conversation_history: list[ConversationTurn]`.
- Output: `PortfolioQueryResponse` (`answer` or `redirect_message`, plus `guardrail_triggered`).

## Depends on

- `anthropic` SDK (Claude Haiku); `ANTHROPIC_API_KEY` env var.
- `pyyaml`, `python-dotenv`.
- `AI_Agents/Reference_docs/market_commentary_latest.md` (written by the `market_commentary` agent).

## Don't read

- `__pycache__/`
- `portfolio_query.md`, `guardrails.md` — runtime prompt/rule sources, not documentation.

## Refresh

If stale, run `/refresh-context` from this folder.
