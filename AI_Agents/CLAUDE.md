# AI_Agents/ — Bundled agent package

Bundled Python package hosting all Prozpr AI agent pipelines, runtime reference data, and archived implementations. Integrated into the FastAPI backend via `sys.path` injection — the `app/` layer never imports `AI_Agents.*` qualified paths; it imports modules under `AI_Agents/src/` directly after the helper has prepended that directory to `sys.path`.

## Child modules

- **src/** — active agent pipelines; each subfolder is one self-contained agent. See `src/CLAUDE.md` for the module map.
- **Reference_docs/** — runtime data consumed by agents (market commentary cache, fund ranking). Treated as runtime data, not committed source — agents may overwrite these on a schedule.
- **archive/** — historical agent implementations; not on active import paths.

## Conventions

- **`sys.path` injection.** Agents under `src/` are loaded via `app/services/ai_bridge/common.ensure_ai_agents_path()`, which prepends `AI_Agents/src/` to `sys.path`. Always go through that helper rather than mutating `sys.path` directly. Once it has run, an agent like `Rebalancing` is imported as `from Rebalancing.models import RebalancingComputeResponse`, not `from AI_Agents.src.Rebalancing.models import ...`.
- **One agent per top-level `src/` folder.** Each agent owns its pydantic input/output models, prompts, and pipeline. Agents are peers — they do not import each other (with the documented exceptions in `src/CLAUDE.md`).
- **LLM calls go through LangChain** — see root `CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.egg-info/`
