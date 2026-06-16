# AI_Agents/src/portfolio_query/ — answer client questions about their own portfolio

Self-contained agent. Combines market commentary + client profile + current portfolio (asset-class, sub-category, per-fund), applies scope guardrails, and returns a factual in-scope answer or a canned redirect.

## Entry / contract
- `orchestrator.py` exposes `PortfolioQueryOrchestrator.run(question, client, portfolio, conversation_history?)` → `PortfolioQueryResponse` (`answer` or `redirect_message` + `guardrail_triggered`).

## Files
- `orchestrator.py` — orchestrator + INR enrichment; reads `Reference_docs/market_commentary_latest.md`.
- `skill_executor.py` — renders a skill `.md` (YAML front matter + System/User sections) into prompts.
- `llm_client.py` — `ChatAnthropic` wrapper with prompt caching and forced tool-use.
- `models.py` — pydantic context/response models.
- `portfolio_query.md` / `guardrails.md` — runtime prompt + scope-rule sources (see Gotchas).
- `dev_run.py` — smoke test. `README.md` — human guide.

## Gotchas & invariants
- `portfolio_query.md` (the skill) and `guardrails.md` are loaded at runtime and rendered verbatim into the system prompt; together they DEFINE the in-scope/out-of-scope (Path X/M/P) decision and the mandatory "Portfolio Impact" paragraph. Editing them changes model behavior with no code change (`orchestrator.py` `__init__` + `run`).
- Every `*_inr` field gets a pre-computed `*_indian` sibling via `format_inr_indian` because Haiku mis-converts lakh/crore; the prompt instructs the model to copy them verbatim (`orchestrator.py` `_enrich_inr_fields`).

## Don't read
- `__pycache__/`.
