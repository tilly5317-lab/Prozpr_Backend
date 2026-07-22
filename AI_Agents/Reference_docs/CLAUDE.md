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
- **Reference docs are refreshed by a human, never automatically.** A change to an engine's logic or its production wiring is **complete without touching any doc** under `Tech_reference_docs/`, `Logics_reference_docs/`, or `Module_reference_docs/`. Do NOT rewrite, version-bump, or re-date these docs as a side effect of a code change, and do not go hunting for drift — refresh a doc only when a human explicitly asks for it. This is a deliberate tradeoff (lower token cost over always-current docs); the **code is the source of truth**. Note that `Logics_reference_docs/*.md` are injected into customer-facing chat answers, so whoever triggers a refresh owns their accuracy. When a human does ask you to edit a Module guide, its content lives in the `source-md` block; the HTML shell is generated, never hand-edited.

## Don't read

- `*.json` / cached `*.md` artifacts when reviewing for code changes — they're outputs, not source.
