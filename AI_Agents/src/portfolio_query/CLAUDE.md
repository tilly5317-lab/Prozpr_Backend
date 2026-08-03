# AI_Agents/src/portfolio_query/ — answer client questions about their own portfolio

Self-contained agent. Combines market commentary + client profile + current portfolio (asset-class, sub-category, per-fund), applies scope guardrails, and returns a factual in-scope answer or a canned redirect.

## Entry / contract
- `orchestrator.py` exposes `PortfolioQueryOrchestrator.run(question, client, portfolio, conversation_history?, want_market_commentary=True)` → `PortfolioQueryResponse` (`answer` or `redirect_message` + `guardrail_triggered`, plus `path` and `suggested_intent`).
- `path` (X/M/P) and `suggested_intent` are TELEMETRY ONLY — REPORTED, NEVER ACTED ON. Nothing branches on either (`models.py`; `portfolio_query_service.py`).

## Files
- `orchestrator.py` — orchestrator + INR enrichment; reads `Reference_docs/market_commentary_latest.md` **only when `want_market_commentary`**. When it does read: a missing or empty file raises before any LLM call, so the agent answers nothing — not even a pure portfolio question; the commentary is truncated to 7,000 chars so it can't dominate the prompt (`_load_market_commentary`).
- `skill_executor.py` — renders a skill `.md` (YAML front matter + System/User sections) into prompts.
- `llm_client.py` — `ChatAnthropic` wrapper with prompt caching, forced tool-use, `temperature=0`, and an optional `stream_field` streaming path.
- `models.py` — pydantic context/response models.
- `portfolio_query.md` / `guardrails.md` — runtime prompt + scope-rule sources (see Gotchas).
- `dev_run.py` — smoke test. `README.md` — human guide.

- The guardrail is not prompt-only: `PortfolioQueryResponse` has a deterministic backstop — when `guardrail_triggered` is true the validator nulls `answer` and substitutes `_DEFAULT_REDIRECT` if none was given. The bridge renders `answer or redirect_message`, so a populated `answer` always wins; this is what stops an out-of-scope answer reaching the customer when the LLM forgets to null it (`models.py` `_enforce_guardrail_contract`).
- `portfolio_query.md` (the skill) and `guardrails.md` are loaded at runtime and rendered verbatim into the system prompt; together they DEFINE the in-scope/out-of-scope (Path X/M/P) decision and the mandatory "Portfolio Impact" paragraph. Editing them changes model behavior with no code change (`orchestrator.py` `__init__` + `run`).
- **The commentary is GATED by the classifier's `tools_needed`.** Loading it unconditionally made the model compare a 26.11% small-cap allocation against a "35.5x expensive valuation" — a percentage against a P/E. When not wanted, `_COMMENTARY_NOT_REQUESTED` is substituted: answer from holdings/profile, don't speculate, and don't mention it's missing. Default `True` keeps non-chat callers unchanged (`orchestrator.py`).
- **`stream_field` has no default on purpose** — the caller must name the customer-facing field. Only `answer` qualifies; `redirect_message` is substituted wholesale by the guardrail backstop, so streaming it would paint text the validator may then null (`llm_client.py`).
- **Streamed turns report usage on `usage_metadata`,** leaving `response_metadata["usage"]` empty; `llm_client`'s fallback is what stops cost accounting recording zeros for every streamed turn (`llm_client.py`).
- **Two XIRRs reach the prompt:** `holdings[].xirr_pct` is one fund's, portfolio-level `xirr_pct` is the whole portfolio's. Per-fund comes from `UserMfLatestSnapshot` keyed by `scheme_code` (= the holding's `ticker_symbol`); the lookup returns `{}` on failure, so a missing XIRR degrades one line rather than failing the turn (`app/domains/portfolio/services/portfolio_query_service.py`).
- Every `*_inr` field gets a pre-computed `*_indian` sibling via `format_inr_indian` because Haiku mis-converts lakh/crore; the prompt instructs the model to copy them verbatim (`orchestrator.py` `_enrich_inr_fields`).

## Don't read
- `__pycache__/`.
