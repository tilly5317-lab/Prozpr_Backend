# AI_Agents/src — Module Map

Python package hosting the Prozpr AI financial-advisor agents. Each top-level folder is a self-contained agent: pydantic input → structured pydantic output. The FastAPI layer composes them; within `src/` agents are peers and do not import each other (exceptions in **Cross-module edges**).

## Files at this level

- `common.py` — cross-agent utilities; stdlib-only, imports no peer agent. The `app/` layer re-imports its helpers — edit here, app follows (`app/domains/ai_engine/common.py`, `app/domains/profile/models/risk_profile.py`).
- `persona.py` — single source of truth for Ask PI's customer-facing voice, re-exported via `app/domains/ai_engine/persona.py` (edit here, app follows); build a surface's prompt with `build_system_prompt(...)`.
- `reasoned_reply.py` — shared reasoned-reply helper for free-text surfaces: the thinking field is declared FIRST (load-bearing), discarded, never returned to the customer.
- `token_stream.py` — answer-token stream for one chat turn (`open_token_stream`, `astream_tool_answer`, the fine-grained-tool-streaming beta). Lives here because `portfolio_query`/`mutual_fund_query` are agents and can't import `app/`; re-exported by `app/domains/ai_engine/streaming.py`. Nothing streams unless a stream is open.

## Child modules

- **asset_allocation_pydantic/** — pure-Python goal-based allocation pipeline over pydantic models; the one LLM touch is step 7's per-bucket rationale, and it is **opt-out, not opt-in**: with no `rationale_fn`, `step7_presentation` defaults to `_rationale_llm.generate_rationales` (ChatAnthropic), degrading to deterministic text if the call fails. Pass `_rationale_llm.no_llm_rationale_fn` to skip the LLM outright. Entry: `pipeline.py`.
- **cashflow_statement/** — goal-planning engine (8-stage pure-Python pipeline) + LangChain agent for NL goal extraction and lever proposal, validated against an Excel-parity baseline. Entry: `engine/pipeline.py`, `agent/graph.py`. See `cashflow_statement/CLAUDE.md`.
- **financial_primitives/** — shared numerical kernel (TVM, annuity, inflation, Indian FY dates, retirement, XIRR). Pure functions, no LLM, no I/O. Library not agent; not an LLM tool. See `financial_primitives/CLAUDE.md`.
- **Rebalancing/** — pure-Python rebalancing engine; takes an ideal allocation + current holdings, emits per-fund target/buy/sell under per-fund caps with tax-aware sell prioritisation. Entry: `pipeline.py`. See `Rebalancing/CLAUDE.md`.
- **intent_classifier/** — classifies a customer question into one of nine intents using Claude Haiku + structured output; also emits `tools_needed`, an independent list of data the answer stage needs. Entry: `classifier.py`.
- **market_commentary/** — web-search-extracts Indian macro indicators into a structured `MacroSnapshot`, writes the markdown commentary doc to `Reference_docs/`, and answers commentary Q&A (`chat_qa.py`). Entry: `main.py`.
- **portfolio_query/** — answers client questions about their **own** portfolio from client profile + holdings (plus market commentary only when `tools_needed` asks for it), with in/out-of-scope guardrails. Entry: `orchestrator.py`. See `portfolio_query/CLAUDE.md`.
- **mutual_fund_query/** — answers questions about **funds themselves**, held or not: returns, peer comparison, why we recommend one, plus a "best in category" screen. Two forced-tool Haiku passes (extract → narrate); DB-agnostic — the app layer builds the facts. Entry: `orchestrator.py`. See `mutual_fund_query/CLAUDE.md`.
- **practical_asset_allocation/** — holdings-aware allocation. Wraps `asset_allocation_pydantic` with four extra corpus inputs; reimplements long-term with ELSS freeze, non-MF-equity NFA-banded cap, and v2 equity-subgroup slider. Entry: `pipeline.py`. See `practical_asset_allocation/CLAUDE.md`.
- **additional_investment/** — pure-Python engine that deploys fresh money into specific funds (BUY-only): lumpsum-with-holdings fills deficits vs the post-investment practical ideal, SIP follows the ideal mix into the latest rebalancing run's BUY funds; emits a per-fund BUY list. Entry: `pipeline.py`. See `additional_investment/CLAUDE.md`.
- **risk_profiling/** — deterministic scoring of a client's risk profile (inputs → scores/flags) + an LLM-generated summary paragraph. Entry: `main.py`.
- **chat_eval/** — (gitignored; dev-only) eval harness: replays a YAML question set through the chat pipeline, emits JSON/HTML reports. Entry: `run_eval.py`.

> `drift_analysis/` lives in `AI_Agents/archive/`, not here.

## Cross-module edges

- `intent_classifier/` returns a string label only; routing happens outside `src/` (no peer imports).
- `portfolio_query/` reads `AI_Agents/Reference_docs/market_commentary_latest.md` (written by `market_commentary/`) but does not import the `market_commentary` module — the file is the contract.
- `asset_allocation_pydantic/`'s `AllocationInput` carries fields from `risk_profiling/` and a `market_commentary` score block from `market_commentary/`, but imports neither — the caller wires them in.
- `practical_asset_allocation/` imports from `asset_allocation_pydantic/` — **the first explicit cross-agent import** under `src/`, blessed by spec §B.1.
- `Rebalancing/` imports `run_practical_allocation` from `practical_asset_allocation/` (Part C of the same spec); `pipeline.run_rebalancing` calls it first and surfaces its output on `RebalancingComputeResponse.practical_allocation`.
- `additional_investment/` is a pure engine that imports no peer agent — its `AdditionalInvestmentInput` is the contract; the app-layer adapter lifts that data from `practical_asset_allocation/`, `cashflow_statement/`, and the fund-ranking CSV.
- All other *agents* are import-independent. (`financial_primitives` is a library, importable by any agent; today only `cashflow_statement/engine` does.)

## Conventions

- Per-module file roles: `models.py` (pydantic I/O schemas), `prompts.py` (prompt strings / `ChatPromptTemplate`), `main.py` (LCEL chain), `orchestrator.py` (class-based orchestrator's top-level class).
- **Every `ChatAnthropic(...)` pins `temperature=0` as a literal.** Unset applies the API default of 1.0 — that returned different rupee figures run to run and flipped 4 of 101 classifier labels. `test_temperature_is_pinned.py` scans the call text repo-wide.
- `references/` — markdown/CSV domain references consumed by prompts. Not product docs. `Testing/` — pytest suites + sample runners. `dev_run.py` — developer smoke-test (`python -m <module>.dev_run`).
- Prompt-adjacent `.md` files are runtime skill/prompt sources, not docs.
- LLM calls go through LangChain — see root `CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
- `../archive/` — historical modules, not the active pipeline
- `docs/` — local planning scaffolding, not agent code
