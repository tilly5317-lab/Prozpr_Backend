# AI_Agents/src — Module Map

Python package hosting the Prozpr AI financial-advisor agents. Each top-level folder is a self-contained agent: pydantic input → structured pydantic output. The FastAPI layer composes them; within `src/` agents are peers and do not import each other (exceptions in **Cross-module edges**).

## Files at this level

- `common.py` — cross-agent utilities; stdlib-only, no peer imports. Re-imported by `app/` — edit here, app follows (`app/domains/ai_engine/common.py`, `app/domains/profile/models/risk_profile.py`).
- `persona.py` — single source of truth for Ask PI's customer-facing voice, re-exported via `app/domains/ai_engine/persona.py` (edit here, app follows); build a surface's prompt with `build_system_prompt(...)`.
- `reasoned_reply.py` — shared reasoned-reply helper for free-text surfaces: the thinking field is declared FIRST (load-bearing), discarded, never returned to the customer.
- `token_stream.py` — answer-token stream for one chat turn (`open_token_stream`, `astream_tool_answer`). Lives here because `portfolio_query`/`mutual_fund_query` can't import `app/`; re-exported by `app/domains/ai_engine/streaming.py`. Nothing streams unless a stream is open.

## Child modules

- **asset_allocation_pydantic/** — pure-Python goal-based allocation over pydantic models; its one LLM touch (step-7 rationale) is opt-OUT. See `asset_allocation_pydantic/CLAUDE.md`.
- **cashflow_statement/** — goal-planning pipeline (pure-Python) + LangChain agent for NL goal extraction and lever proposal. See `cashflow_statement/CLAUDE.md`.
- **financial_primitives/** — shared numerical kernel; pure functions, no LLM/I/O — a library, not an agent. See `financial_primitives/CLAUDE.md`.
- **Rebalancing/** — pure-Python engine: ideal allocation + holdings → per-fund buy/sell with tax-aware sell prioritisation. See `Rebalancing/CLAUDE.md`.
- **intent_classifier/** — classifies a question into one of nine intents (Haiku + structured output); also emits `tools_needed`, the data the answer stage needs. Entry: `classifier.py`.
- **market_commentary/** — web-search-extracts Indian macro indicators into a `MacroSnapshot`, writes the commentary doc to `Reference_docs/`, answers commentary Q&A. Entry: `main.py`.
- **portfolio_query/** — builds the facts pack (client profile + holdings) and owns the skill prompt + scope guardrails. See `portfolio_query/CLAUDE.md`.
- **mutual_fund_query/** — questions about **funds themselves**, held or not; forced-tool Haiku extract, DB-agnostic. See `mutual_fund_query/CLAUDE.md`.
- **practical_asset_allocation/** — holdings-aware allocation: wraps `asset_allocation_pydantic` with four extra corpus inputs. See `practical_asset_allocation/CLAUDE.md`.
- **additional_investment/** — pure-Python engine deploying fresh money (BUY-only): lumpsum fills deficits, SIP follows the ideal mix. See `additional_investment/CLAUDE.md`.
- **risk_profiling/** — deterministic risk-profile scoring (inputs → scores/flags) + an LLM-generated summary paragraph. Entry: `main.py`.
- **chat_eval/** — (gitignored; dev-only) eval harness: replays a YAML question set through the chat pipeline. Entry: `run_eval.py`.

> `drift_analysis/` lives in `AI_Agents/archive/`, not here.

## Cross-module edges

- `intent_classifier/` returns a label plus `tools_needed` (which market context the answer needs: `market_commentary` facts vs `fund_house_view`); routing/consumption happens outside `src/` (no peer imports).
- `portfolio_query/` reads `AI_Agents/Reference_docs/market_commentary_latest.md` (facts, written by `market_commentary/`) and `fund_house_commentry.md` (Prozpr's hand-maintained monthly view) but imports neither — the files are the contract; which loads is gated by the classifier's `tools_needed` (`market_commentary` vs `fund_house_view`).
- `asset_allocation_pydantic/`'s `AllocationInput` carries fields from `risk_profiling/` and a `market_commentary` score block, but imports neither — the caller wires them in.
- `practical_asset_allocation/` imports from `asset_allocation_pydantic/` — **the first explicit cross-agent import** under `src/`, blessed by spec §B.1.
- `Rebalancing/` imports `run_practical_allocation` from `practical_asset_allocation/` (Part C of the same spec); `run_rebalancing` calls it first.
- `additional_investment/` imports no peer agent — its `AdditionalInvestmentInput` is the contract; the app-layer adapter lifts that data from `practical_asset_allocation/`, `cashflow_statement/`, and the fund-ranking CSV.
- All other agents are import-independent. (`financial_primitives` is a library importable by any agent; today only `cashflow_statement/engine` does.)

## Conventions

- **Agents produce facts and prompts; `app/domains/ai_engine/answer_formatter` writes every customer-facing reply.** An agent under `src/` may make its own LLM calls for INTERNAL steps (classify, extract, research) but no longer composes the chat answer. Consequence: `portfolio_query` and `mutual_fund_query` can't answer standalone — drive a real chat turn to exercise them.
- Per-module file roles: `models.py` (pydantic I/O schemas), `prompts.py` (prompt strings / `ChatPromptTemplate`), `main.py` (LCEL chain), `orchestrator.py` (class-based orchestrator's top-level class).
- **Every `ChatAnthropic(...)` pins `temperature=0` as a literal.** Unset applies the API default of 1.0 — nondeterministic outputs (root `CLAUDE.md` has the full rationale). `test_temperature_is_pinned.py` scans the call text repo-wide.
- `Testing/` — pytest suites + sample runners. `dev_run.py` — developer smoke-test (`python -m <module>.dev_run`). Prompt-adjacent `.md` files are runtime skill/prompt sources, not docs.
- LLM calls go through LangChain — see root `CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
- `../archive/` — historical modules, not the active pipeline
- `docs/` — local planning scaffolding, not agent code
