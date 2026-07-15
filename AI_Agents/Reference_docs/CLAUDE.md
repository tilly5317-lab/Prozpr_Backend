# AI_Agents/Reference_docs — Reference-doc index

Runtime reference data read by agents under `AI_Agents/src/` (market-commentary cache, fund ranking), plus three subfolders of human-facing docs.

## Files

- `market_commentary_latest.md` — daily Indian macro commentary. Written by `src/market_commentary/` (driven by `app/domains/market_commentary/services/market_commentary_engine.py`); read by `src/portfolio_query/` for its commentary context block.
- `market_commentary_latest.json` — `MacroSnapshot` cache backing the `.md` (TTL via `MARKET_COMMENTARY_CACHE_MAX_AGE_SEC`).
- `prozpr_fund_ranking_may_2026.csv` — fund-ranking table. `rank ≥ 1` + `selection_reason` = recommended; rank-blank rows are evaluated-then-rejected, with per-row `*_reason` columns. Consumed by `app/domains/rebalancing/services/rebal_engine/fund_rank.py` (`get_fund_ranking`, `get_rejection_reasons`).
- **Tech_reference_docs/** — engineer-facing docs, not runtime data: `ARCHITECTURE.md` (hand-authored walkthrough of `AI_Agents/`: per-agent contracts, deterministic-vs-LLM split, cross-agent data-flow, landmines) + its browser rendering `ARCHITECTURE.html`, and `CHAT_FLOW.html` (non-technical chat-flow guide).
- **Logics_reference_docs/** — directional, client-safe thesis docs — philosophy and approach only; they deliberately omit proprietary weights, bands, thresholds (for engine-true numbers, read the code): `Asset_Allocation.md` (v1.4), `Practical_Asset_Allocation.md`, `Risk_Profiling.md`, `Rebalancing.md`, `Cashflow_Statement.md`, `Additional_Investment.md`, `Mutual_Fund_Query.md` (each v1.x; drift-audited against engine code 2026-07-05).
- **Module_reference_docs/** — engineer-facing per-module deep-dives (added 2026-07-12): one self-rendering HTML guide per active `src/` agent (10) + `index.html`. Each follows a fixed 9-section template (what it's about → where it sits → input/output contracts → how it thinks → where the LLM is → file map → invariants → extend & test) with an at-a-glance card, a worked example, and code-pinned relative links. Self-render from an embedded `<script type="text/markdown" id="source-md">` block (edit that block; marked.js + mermaid via CDN, shell shared across all the HTML docs). More code-level than the Logics theses; more per-module than `Tech_reference_docs/ARCHITECTURE.html`.

## Gotchas & invariants

- Top-level files are runtime data, not committed source — agents overwrite them on a schedule (`src/market_commentary/main.py` writes both `market_commentary_latest.*`). Add a new top-level file only when an AI module needs it as input; human docs belong in the subfolders.
- **Logics docs must track the engines.** `Logics_reference_docs/*.md` ground customer-facing LLM chat answers, so a change to an engine's logic *or its production wiring* (features toggled on/off in an input builder count) is not done until the matching thesis doc is updated and its version/date bumped — a stale doc becomes a confidently wrong customer answer. Describe production behaviour, not dormant engine capability.
- **Module guides track engine internals.** `Module_reference_docs/*.html` describe how each engine actually works (steps, contracts, invariants) for engineers. Not a customer-correctness gate like the Logics docs, but an engine change can make the matching guide's affected section stale — update that section + the "reconciled on" date when you change an engine (the footer already tells readers to trust the code and flag drift). Content lives in the `source-md` block; the HTML shell is generated, never hand-edited.

## Don't read

- `*.json` / cached `*.md` artifacts when reviewing for code changes — they're outputs, not source.
