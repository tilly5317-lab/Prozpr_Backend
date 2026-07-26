# AI_Agents/src/market_commentary/ — monthly Indian-market commentary generator

Gathers 14 macro indicators via Claude + Anthropic web_search, extracts them into a `MacroSnapshot`, then renders a markdown commentary. Consumed downstream by `asset_allocation_pydantic/` (caller-supplied score fields) and read as a file by `portfolio_query/`.

## Entry / contract
- `main.py` exposes `MarketCommentaryAgent(api_key, output_dir, generate_document=True)`; the caller points `output_dir` at `AI_Agents/Reference_docs/`. Production calls `run_from_cache(max_age_sec)` first and only falls through to `.run()` (websearch → extract → cache → doc-gen). `.run()` writes `market_commentary_latest.json` only when the snapshot is non-empty — an all-null run with no cache on disk leaves the JSON untouched but still regenerates the `.md` from nulls — and writes the `.md` only when `generate_document=True`. `_DOCUMENT_FILENAME` / `_CACHE_FILENAME` name those files (`main.py`).

## Files
- `main.py` — agent, web-search extraction, `CacheManager`.
- `document_generator.py` — `generate_document` / `DocumentGenerator`; `MacroSnapshot` → markdown (forced tool-use via `reasoned_reply`).
- `models.py` — `MacroSnapshot` (indicator fields, `data_gaps`, `document_md`).
- `prompts.py` — extraction + doc-gen prompts. `chat_qa.py` — optional Q&A over the commentary.
- `README.md` — human guide. `_archive/` — retired scraper + old snapshots, not active source.

## Gotchas & invariants
- `.md` is regenerated from the cached snapshot only when missing or older than the JSON; otherwise the existing file is returned verbatim (no LLM call) — the document is a deterministic function of the snapshot (`main.py` `run_from_cache`).
- If web-search yields an all-null snapshot, the pipeline falls back to the cached one and tags `data_gaps` with `ALL_LIVE_DATA_FAILED` (`main.py` `run`).

## Don't read
- `__pycache__/`.
- `_archive/` — historical snapshots and retired scraper.
- `test_chat_qa_reasoning.py` / `test_document_generator_reasoning.py` — root-level reasoning test scripts.
