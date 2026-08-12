# AI_Agents/src/mutual_fund_query/ — answer questions about funds themselves

Self-contained agent. Given a question about one or more mutual funds — held or not — it names the fund(s), then narrates a grounded answer from facts the app layer assembled: returns, our house reason for shortlisting it, and peer rows. DB-agnostic: it only ever sees DTOs.

## Entry / contract
- `orchestrator.py` exposes `MutualFundQueryOrchestrator.extract(question, history?)` → `ExtractResult` and `narrate_body` — the Narrate System section, which the app bridge passes to the shared answer formatter as its `body_prompt`. Editing the skill `.md` still changes behaviour with no code change.
- **Two passes, no agentic loop.** Pass 1 (here) identifies the fund(s) and must never answer; pass 2 is the shared formatter (`action_mode="fund_detail"`).
- The caller owns the middle: it resolves fund names and builds `MutualFundQueryFacts` between the two calls.

## Files
- `orchestrator.py` — the extract pass, `narrate_body`, prompt section parsing, the extract tool schema.
- `models.py` — `ExtractResult`, `MutualFundQueryFacts` / `FundFacts` / `FundReturns` / `PeerFund`.
- `llm_client.py` — `ChatAnthropic` wrapper for the extract pass: forced tool-use, prompt caching, `temperature=0`. No streaming path — extract returns routing metadata, not prose.
- `mutual_fund_query.md` — runtime prompt source: Extract System / Extract User / Narrate System; guardrails embedded in Narrate System, which the app passes to the formatter as `body_prompt`.

## Gotchas & invariants
- **The engine must never state a number that isn't in `facts`.** Enforced by prompt, not code. Widen `MutualFundQueryFacts` only with values you can source — a field the model *can* see, it *will* state.
- **`shortlist_rank` is our house rank, NOT a category percentile.** The name invites "ranks 3 of 24", which is false. `has_house_view` is what says whether we have an opinion at all; when it's false, give returns and say plainly it isn't one we recommend.
- **Both branches end at the shared formatter, with different modes.** `ExtractResult.is_screen` (a "best funds" ask naming no fund) → `screen_top_funds` + `action_mode="screen"`; a named fund → `build_mutual_fund_query_facts` + `action_mode="fund_detail"`. `fund_detail` is deliberately NOT in `LOGIC_DOC_MODES`: switching it to `narrate` would attach `Mutual_Fund_Query.md` (~888 words) to every fund answer — probably right for "why do we recommend it", but it changes replies, so it needs its own before/after.
- **The prompt file is BOM-aware and split on `---`.** It carries `₹`, em-dashes and inline JSON tool schemas, so it uses `read_text_bom_aware` plus a brace-safe `_fill` — a naive `str.format` chokes on the JSON braces (`orchestrator.py`).
- **Peers only for a single named fund.** Two or more named funds ⇒ `peers` empty and the named funds are the comparison set.

## Don't read
- `__pycache__/`, `Testing/`.
