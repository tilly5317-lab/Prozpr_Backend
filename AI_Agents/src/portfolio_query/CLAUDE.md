# AI_Agents/src/portfolio_query/ — answer client questions about their own portfolio

Self-contained agent. Combines market commentary + client profile + current portfolio (asset-class, sub-category, per-fund), applies scope guardrails, and returns a factual in-scope answer or a canned redirect.

## Entry / contract
- `orchestrator.py` exposes `PortfolioQueryOrchestrator.run(question, client, portfolio, conversation_history?)` → `PortfolioQueryResponse` (`answer` or `redirect_message` + `guardrail_triggered`, plus `path` and `suggested_intent`).
- `path` (X/M/P, set on every turn) and `suggested_intent` (usually null) are TELEMETRY ONLY — REPORTED, NEVER ACTED ON. Nothing downstream branches on either; the app layer logs `path` each answered turn and `suggested_intent` only when it names a different module (`models.py`; `portfolio_query_service.py` `_record_path` / `_record_intent_disagreement`).

## Files
- `orchestrator.py` — orchestrator + INR enrichment; reads `Reference_docs/market_commentary_latest.md`. A missing or empty file raises before any LLM call, so the agent answers nothing — not even a pure portfolio question; the commentary is truncated to 7,000 chars so it can't dominate the prompt (`_load_market_commentary`).
- `skill_executor.py` — renders a skill `.md` (YAML front matter + System/User sections) into prompts.
- `llm_client.py` — `ChatAnthropic` wrapper with prompt caching and forced tool-use.
- `models.py` — pydantic context/response models.
- `portfolio_query.md` / `guardrails.md` — runtime prompt + scope-rule sources (see Gotchas).
- `dev_run.py` — smoke test. `README.md` — human guide.

- The guardrail is not prompt-only: `PortfolioQueryResponse` has a deterministic backstop — when `guardrail_triggered` is true the validator nulls `answer` and substitutes `_DEFAULT_REDIRECT` if none was given. The bridge renders `answer or redirect_message`, so a populated `answer` always wins; this is what stops an out-of-scope answer reaching the customer when the LLM forgets to null it (`models.py` `_enforce_guardrail_contract`).
- `portfolio_query.md` (the skill) and `guardrails.md` are loaded at runtime and rendered verbatim into the system prompt; together they DEFINE the in-scope/out-of-scope (Path X/M/P) decision and the mandatory "Portfolio Impact" paragraph. Editing them changes model behavior with no code change (`orchestrator.py` `__init__` + `run`).
- Every `*_inr` field gets a pre-computed `*_indian` sibling via `format_inr_indian` because Haiku mis-converts lakh/crore; the prompt instructs the model to copy them verbatim (`orchestrator.py` `_enrich_inr_fields`).

## Don't read
- `__pycache__/`.
