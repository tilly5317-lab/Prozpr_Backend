# AI_Agents/src/portfolio_query/ — answer client questions about their own portfolio

Prepares the portfolio_query turn but does NOT answer it. Assembles market commentary (factual) + fund-house view + client profile + current portfolio (asset-class, sub-category, per-fund) into a facts pack, and owns the scope-guardrail prompt; the reply is written by the shared answer formatter in `app/`.

## Entry / contract
- `orchestrator.py` exposes `build_facts(client, portfolio, want_market_commentary=True, want_fund_house_view=False)` → the INR-enriched facts dict, and `query_body` → the skill's System Prompt with `guardrails.md` filled in. The app bridge passes both to the shared answer formatter, which makes the LLM call and writes the reply (`action_mode="narrate"`).
- `path` (X/M/P) and `suggested_intent` are TELEMETRY ONLY — REPORTED, NEVER ACTED ON. Nothing branches on either (`models.py`; `portfolio_query_service.py`).

## Files
- `orchestrator.py` — `query_body` + `build_facts` + INR enrichment. Reads `Reference_docs/market_commentary_latest.md` **only when `want_market_commentary`**; a missing/empty file then raises before any LLM call (the agent answers nothing, not even a pure portfolio question), and the commentary is truncated to 7,000 chars (`_load_market_commentary`).
- `skill_executor.py` — renders a skill `.md` (YAML front matter + System/User sections) into prompts.
- `models.py` — pydantic context models + `_DEFAULT_REDIRECT`, the canned out-of-scope line the bridge falls back to.
- `portfolio_query.md` / `guardrails.md` — runtime prompt + scope-rule sources (see Gotchas).
- `README.md` — human guide. **No `dev_run.py` / `llm_client.py`**: with the answer stage in `app/`, this package cannot answer standalone — drive a real chat turn to exercise it.

## Gotchas & invariants

- **The guardrail is not prompt-only, and the backstop moved.** `guardrail_triggered` / `redirect_message` / `path` / `suggested_intent` ride as `extra_tool_fields` on the formatter's tool. `_apply_guardrail_backstop` in the bridge DISCARDS `answer` whenever the guardrail fires (the model may write the out-of-scope reply before deciding it was Path X) and sends `redirect_message`, else `_DEFAULT_REDIRECT`. The bridge passes `allow_empty_answer=True` so a Path-X null `answer` doesn't read as a formatter failure (`app/domains/portfolio/services/portfolio_query_service.py`).
- `portfolio_query.md` and `guardrails.md` are loaded at runtime and rendered verbatim into the system prompt; together they DEFINE the in/out-of-scope (Path X/M/P) decision and the mandatory "Portfolio Impact" paragraph, so editing them changes behavior with no code change (`orchestrator.py` `query_body`). **The Step 1–5 checklist lives in the System section**, not the User section — the formatter owns the user message.
- **The commentary is GATED by the classifier's `tools_needed`.** Loading it unconditionally made the model compare a small-cap allocation % against a valuation P/E. When not wanted, `_COMMENTARY_NOT_REQUESTED` is substituted: answer from holdings/profile, don't speculate, don't mention it's missing. Default `True` keeps non-chat callers unchanged (`orchestrator.py`). The **fund-house view** rides the same gate (`want_fund_house_view`, default `False`) but degrades to a placeholder instead of raising when its file is absent; when present, the reply cites the named houses as research sources (`_load_fund_house_view`).
- **Streaming and usage accounting happen inside the formatter now.** Only `answer` is ever streamed; `redirect_message` is substituted wholesale by the backstop, so streaming it would paint text that is then discarded.
- **Two XIRRs reach the prompt:** `holdings[].xirr_pct` (one fund's) and portfolio-level `xirr_pct`. Per-fund comes from `UserMfLatestSnapshot` keyed by `scheme_code` (= the holding's `ticker_symbol`); the lookup returns `{}` on failure, so a missing XIRR degrades one line rather than failing the turn (`portfolio_query_service.py`).
- Every `*_inr` field gets a pre-computed `*_indian` sibling via `format_inr_indian` because Haiku mis-converts lakh/crore; the prompt instructs the model to copy them verbatim (`orchestrator.py` `_enrich_inr_fields`, applied inside `build_facts`).
- **History reaches this agent pre-annotated.** The bridge passes `history_override=_build_history(...)` so time gaps are marked and long prior answers excerpted BEFORE the formatter's own 6-turn slice. Without it the agent read a fortnight-old goal question as live context and a 14K-char goal reply swamped the prompt (`portfolio_query_service.py` `_build_history`).

## Don't read
- `__pycache__/`.
