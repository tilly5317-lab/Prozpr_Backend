# AI_Agents/src/mutual_fund_query/ — answer questions about funds themselves

Self-contained agent. Given a question about one or more mutual funds — held or not — it names the fund(s), then narrates a grounded answer from facts the app layer assembled: returns, our house reason for shortlisting it, and peer rows. DB-agnostic: it only ever sees DTOs.

## Entry / contract
- `orchestrator.py` exposes `MutualFundQueryOrchestrator.extract(question, history?)` → `ExtractResult` and `.narrate(facts, question, history?)` → `MutualFundQueryResponse` (`answer` + optional `clarifying_question`).
- **Two passes, both single forced-tool calls — no agentic loop.** Pass 1 identifies; pass 2 answers. Pass 1 must never answer the question itself.
- The caller owns the middle: it resolves fund names and builds `MutualFundQueryFacts` between the two calls.

## Files
- `orchestrator.py` — the two passes, prompt section parsing, tool schemas.
- `models.py` — `ExtractResult`, `MutualFundQueryFacts` / `FundFacts` / `FundReturns` / `PeerFund`, `MutualFundQueryResponse`.
- `llm_client.py` — `ChatAnthropic` wrapper: forced tool-use, prompt caching, `temperature=0`, optional `stream_field`.
- `mutual_fund_query.md` — runtime prompt source, one file split into four `---` sections (Extract System / Extract User / Narrate System / Narrate User); guardrails embedded in Narrate System.

## Gotchas & invariants
- **The engine must never state a number that isn't in `facts`.** Enforced by prompt, not code. Widen `MutualFundQueryFacts` only with values you can source — a field the model *can* see, it *will* state.
- **`shortlist_rank` is our house rank, NOT a category percentile.** The name invites "ranks 3 of 24", which is false. `has_house_view` is what says whether we have an opinion at all; when it's false, give returns and say plainly it isn't one we recommend.
- **The screen path never reaches `narrate`.** `ExtractResult.is_screen` (a "best funds" ask naming no fund) is handled entirely in the app layer via `screen_top_funds` + the shared answer formatter (`action_mode="screen"`).
- **`stream_field` has no default on purpose** — the caller must name the customer-facing field so an internal one is never streamed by accident. Only `narrate` passes one (`"answer"`); `extract` produces routing metadata, not prose.
- **Streamed turns report usage on `usage_metadata`,** leaving `response_metadata["usage"]` empty; `llm_client`'s fallback is what stops token/cost accounting recording zeros for every streamed turn (`llm_client.py`).
- **The prompt file is BOM-aware and split on `---`.** It carries `₹`, em-dashes and inline JSON tool schemas, so it uses `read_text_bom_aware` plus a brace-safe `_fill` — a naive `str.format` chokes on the JSON braces (`orchestrator.py`).
- **Peers only for a single named fund.** Two or more named funds ⇒ `peers` empty and the named funds are the comparison set.

## Don't read
- `__pycache__/`, `Testing/`.
