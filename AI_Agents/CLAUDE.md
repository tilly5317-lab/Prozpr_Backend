# AI_Agents/ — Bundled agent package

Hosts all Prozpr AI agent pipelines, runtime reference data, and archived implementations. Integrated into the backend via `sys.path` injection: the `app/` layer imports modules under `src/` directly (e.g. `from Rebalancing.models import …`), never via `AI_Agents.*` qualified paths.

## Child modules

- **src/** — active agent pipelines; each subfolder is one self-contained agent. See `src/CLAUDE.md` for the module map.
- **Reference_docs/** — runtime data consumed by agents (market-commentary cache, fund ranking) plus human-facing docs: the `ARCHITECTURE` walkthrough, per-module engineer guides (`Module_reference_docs/`), and client-facing Logics theses. Agents may overwrite the cache files on a schedule. See `Reference_docs/CLAUDE.md`.
- **archive/** — historical agent implementations; not on active import paths.
- **lifecycle_sim_testing/** — DEV-ONLY (gitignored) multi-year lifecycle simulation harness: replays the engines over a simulated portfolio and writes HTML reports. Not imported by runtime.
- **tests/** — pytest/eval harness for the bundled agents: a reusable suite runner (`_eval_harness.py`) + its self-tests, an intent-classifier test, and a `cashflow_statement/` eval subfolder. Not imported by runtime.

## Conventions

- **`sys.path` injection.** Agents under `src/` are loaded via `app.domains.ai_engine.common.ensure_ai_agents_path()`, which prepends `AI_Agents/src/` to `sys.path`. Always go through that helper rather than mutating `sys.path` directly.
- **One agent per top-level `src/` folder.** Each owns its pydantic I/O models, prompts, and pipeline. Agents are peers — they do not import each other (documented exceptions in `src/CLAUDE.md`).
- **LLM calls go through LangChain** — see root `CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
