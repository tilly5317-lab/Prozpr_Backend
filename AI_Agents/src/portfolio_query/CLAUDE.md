# AI_Agents/src/portfolio_query

Handles the `portfolio_query` intent: answers client questions about their own portfolio using three context sources — the fund house's monthly market outlook, the client profile, and the client's current portfolio. Applies scope guardrails and returns either a factual in-scope answer or a canned redirect.

## Files

- `orchestrator.py` — `PortfolioQueryOrchestrator`; entry point for the portfolio-query pipeline.
- `models.py` — `ConversationTurn`, `PortfolioQueryInput`, `PortfolioQueryResponse`.
- `dev_run.py` — developer smoke-test covering in-scope multi-turn and guardrail-trigger scenarios.
- `portfolio_query.md` — prompt-adjacent skill source (system + user prompts) loaded at runtime; not documentation.
- `guardrails.md` — prompt-adjacent scope-rule source embedded into the skill's system prompt; not documentation.

## Data contract

- Input: `PortfolioQueryInput` (plus `ClientProfile` and `Portfolio` from `allocation.schemas`)
- Output: `PortfolioQueryResponse`

## Depends on

- `allocation/` — `common.llm_client.LLMClient`, `utilities.fund_view_loader.FundViewLoader`, `skills.executor.SkillExecutor`, `schemas.client_profile.ClientProfile`, `schemas.portfolio.Portfolio`
- Claude Haiku via `allocation.common.llm_client`; `ANTHROPIC_API_KEY` env var; `data/fund_view.txt`

## Don't read

- `__pycache__/`
- `portfolio_query.md`, `guardrails.md` — runtime prompt/rule sources, not documentation

## Refresh

If stale, run `/refresh-context` from this folder.
