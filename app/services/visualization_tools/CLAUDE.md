# app/services/visualization_tools/ — Chart-spec builders

Per-chart packages that produce typed Pydantic `ChartPayload` objects consumed
by chat bridges. `registry.py` is the central index — the chat selector LLM
reads each entry's `description` to decide which chart(s) to attach to a
reply; the dispatcher in `chat_core/brain.py` then invokes the matching
`builder`.

## Files

- `registry.py` — central registry of chart tools. Each entry carries `name`, `description`, `builder` (async callable → typed payload or `None`), and `payload_cls`. Adding a chart = create a per-folder package and register one entry.
- `conftest.py` — pytest fixtures shared across chart-package test suites.

## Child packages

- **category_gap_bar/** — `builder.py` returns a `CategoryGapBar` payload comparing planned vs current allocation by sub-category. Has `tests/`.
- **buy_sell_ledger/**, **concentration_risk/**, **current_donut/**, **planned_donut/**, **profile_dial/**, **target_vs_actual/**, **tax_cost_bar/**, **top_bottom_funds/** — referenced by `registry.py`. Source files are not currently checked in (directories hold only `__pycache__/` from prior builds); treat as work-in-progress / pending re-implementation.

## Conventions

- One folder per chart family. Each provides `builder.py` (async builder) and `schema.py` (Pydantic payload class).
- Builders return `None` when source data is missing rather than raising —
  the selector then drops the chart from the reply.
- Payload classes are carried explicitly in `registry.py` because Python 3.9's
  runtime `X | None` syntax breaks `typing.get_type_hints` (see registry
  docstring).

## Don't read

- `__pycache__/` (including stale ones under the orphan subpackages above).
