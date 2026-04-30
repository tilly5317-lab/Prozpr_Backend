# AI_Agents/src/market_commentary

Produces a monthly Indian-market commentary. Pulls raw macro indicator data, uses Claude to extract structured values into a `MacroSnapshot`, and then generates a markdown commentary document from that snapshot. Output is consumed downstream by `goal_based_allocation_pydantic/` via caller-supplied score fields.

## Files

- `main.py` — `MarketCommentaryAgent`; top-level entry: orchestrates extraction and document generation.
- `models.py` — `MacroSnapshot` (indicator fields, `data_gaps`, `document_md`).
- `prompts.py` — extraction prompt and document-generation prompt templates.
- `document_generator.py` — `document_generation_chain`; turns a `MacroSnapshot` into markdown.
- `chat_qa.py` — optional Q&A over the generated commentary.
- `_archive/` — archived scraper and prior snapshot files; not active source.

## Data contract

- Input: none (scraper-driven; no external pydantic input model)
- Output: `MacroSnapshot` (with `document_md` populated after generation)
- Persisted artifacts (`market_commentary_latest.json`, `market_commentary_latest.md`) are written to `AI_Agents/Reference_docs/`, the canonical home for AI-module reference docs.

## Depends on

- `langchain-anthropic`, Claude (extraction and generation)
- `python-dotenv`; `ANTHROPIC_API_KEY` env var
- Web-search / HTTP libraries for indicator scraping

## Don't read

- `__pycache__/`
- `_archive/` — historical snapshots and retired scraper

## Refresh

If stale, run `/refresh-context` from this folder.
