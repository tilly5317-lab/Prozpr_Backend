# AI_Agents/Reference_docs — Reference-doc index

Runtime reference data read by agents under `AI_Agents/src/` (market-commentary cache, fund ranking), plus two subfolders of human-facing docs.

## Files

- `market_commentary_latest.md` — daily Indian macro commentary. Written by `src/market_commentary/` (driven by `app/domains/market_commentary/services/market_commentary_engine.py`); read by `src/portfolio_query/` for its commentary context block.
- `market_commentary_latest.json` — `MacroSnapshot` cache backing the `.md` (TTL via `MARKET_COMMENTARY_CACHE_MAX_AGE_SEC`).
- `prozpr_fund_ranking_june_2026_v2.csv` — the LIVE fund-ranking table. `rank ≥ 1` + `selection_reason` = recommended; rank-blank rows are evaluated-then-rejected, with per-row `*_reason` columns. Consumed by `app/domains/rebalancing/services/rebal_engine/fund_rank.py` (`get_fund_ranking`, `get_rejection_reasons`).
- `prozpr_fund_ranking_may_2026.csv` — the previous month's table, kept only because `Rebalancing/Testing/test_5_profile_smoke.py` still pins it. No production code reads it — don't confuse the two when updating rankings.
- **Tech_reference_docs/** — engineer-facing docs, not runtime data. `ARCHITECTURE.md` — hand-authored walkthrough of `AI_Agents/`: per-agent contracts, deterministic-vs-LLM split, cross-agent data-flow, landmines. `CHAT_FLOW.md` — non-technical guide to how a customer question travels through the chat. Each has a generated `.html` viewer beside it (see Gotchas — edit the `.md`, never the `.html`).
- **Logics_reference_docs/** — directional, client-safe thesis docs — philosophy and approach only; they deliberately omit proprietary weights, bands, thresholds (for engine-true numbers, read the code): `Asset_Allocation.md`, `Practical_Asset_Allocation.md`, `Risk_Profiling.md`, `Rebalancing.md`, `Cashflow_Statement.md`, `Additional_Investment.md`, `Mutual_Fund_Query.md` (each v1.x; the version footer in each doc is authoritative). Which docs reach a reply is wired in `app/domains/ai_engine/logic_docs.py` — `_MODULE_DOCS`, attached only on `educate`/`narrate` action modes.

## Gotchas & invariants

- **`Tech_reference_docs/*.html` are GENERATED — never hand-edit their prose.** Edit the `.md`, then run `python3 -m scripts.build_reference_docs`. Each `.html` is a viewer shell wrapping its markdown in a `<script type="text/markdown" id="source-md">` block; the generator swaps **only that block**, so the surrounding shell (CSS, `<title>`, the header-strip date) is preserved and *is* the right place to hand-edit presentation. The generator's paths were orphaned by the `d088781f` docs reorg and it silently failed for weeks — both copies drifted into hand-maintenance, which is exactly what this rule prevents (`scripts/build_reference_docs.py`).
- Top-level files are runtime data, not committed source — agents overwrite them on a schedule (`src/market_commentary/main.py` writes both `market_commentary_latest.*`). Add a new top-level file only when an AI module needs it as input; human docs belong in the subfolders.
- **Logics docs must track the engines.** `Logics_reference_docs/*.md` ground customer-facing LLM chat answers, so a change to an engine's logic *or its production wiring* (features toggled on/off in an input builder count) is not done until the matching thesis doc is updated and its version/date bumped — a stale doc becomes a confidently wrong customer answer. Describe production behaviour, not dormant engine capability.

## Don't read

- `*.json` / cached `*.md` artifacts when reviewing for code changes — they're outputs, not source.
