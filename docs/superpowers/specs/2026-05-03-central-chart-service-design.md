# Central Chart Service — design

**Date:** 2026-05-03
**Status:** Design
**Owner:** Amoul
**Builds on:** the existing — but unwired — `app/services/visualization_tools/registry.CHART_TOOLS`, the dead-code `chart_selector_service.select_charts`, the rebalancing-only `ai_bridge/rebalancing/{charts,chart_picker}.py`, and the on-brand design tokens in `Prozpr_Frontend/src/index.css` (`--wealth-navy/blue/green/amber`, `Instrument Serif`, `DM Sans`).

## Summary

There are two parallel chart paths in production today: an unused central registry under `visualization_tools/` (4 AA chart builders, none wired into chat) and a separate rebalancing-only path under `ai_bridge/rebalancing/` (3 chart computers + a Haiku picker on the chat critical path). The frontend renders only the rebalancing set. AA chat replies surface no charts at all. Visual styling on the rebalancing charts uses ad-hoc hex values that don't match the website's `wealth-*` design tokens.

This spec consolidates everything into a single registry-backed service:
- One catalog (`CHART_TOOLS`), one selector (`select_charts`), one typed payload schema (`ChartPayload`), one frontend dispatcher (`ChartRenderer`).
- Both AA and Rebalancing chat paths invoke the same selector, kicked off **in parallel with the formatter LLM** so it adds zero wall-clock latency.
- 9-chart launch catalog: 3 already-typed AA charts (relocated), 3 rebalancing charts migrated from `ai_bridge/rebalancing/` into typed registry entries, 3 net-new builders (`top_bottom_funds`, `profile_dial`, `buy_sell_ledger`). 1 retired (`sub_asset_treemap`).
- Editorial-wealth visual language across all 9 charts: `Instrument Serif` italic titles, `wealth-navy/blue/green/amber` palette, `rounded-2xl` cards with the existing `shadow-wealth`.
- Per-chart folders for cohesion, plus an auto-generated `docs/charts.md` reference for discovery.

## Goals

- Wire AA chat replies to surface charts for the first time, hidden behind the formatter LLM (zero added wall-clock latency).
- Remove the rebalancing chart picker from the critical path, saving ~1-2s per rebalancing turn.
- Single source of truth for chart definitions: each chart owns one folder containing schema + builder + tests; the registry just imports.
- Editorial-wealth visual language applied uniformly to all charts so they read as part of the website, not generic Recharts widgets.
- Make adding a new chart a one-folder change (new folder + register).

## Non-goals (follow-ups, not in this spec)

- Charts for `portfolio_query`, `general_chat`, or `general_market_query`. Selector scope is **AA + Rebalancing only** at launch. Extending later is one-line per intent.
- Net-worth-over-time / goal-progress / sector-exposure / equity-style-box / cashflow / tax-saved / market-snapshot charts. Discussed and deferred — the 9-chart launch set is intentional.
- Storybook-style live preview page for the chart catalog. The auto-generated `docs/charts.md` reference is the launch deliverable; a runtime preview page can come later.
- Streaming charts to the client over SSE/WebSocket after the text reply has rendered. Considered as latency option D; rejected for now because option A (parallel selection) already gives the wins without a frontend protocol change.
- Deterministic keyword pre-filter ("if intent=AA and question matches X, skip the LLM selector"). Defer until we measure selector cost in production.
- Persisted chart-payload normalization for old chat-history rows. Existing `ChatMessage.chart_payloads` rows from rebalancing keep their old shape; `ChartRenderer` just renders whatever comes in keyed off `type`.

## Locked design decisions

| Decision | Choice | Why |
|---|---|---|
| Architecture | Full merge into `visualization_tools/` (option A from brainstorm) | Two parallel chart paths is the root cause of the catalog-visibility pain; a façade only half-fixes it |
| Selector scope | AA + Rebalancing only | Matches today's framing; selector returns empty list when no chart fits, so no risk of intrusive charts in `general_chat` |
| Selector latency | Kick off `select_charts(question, intent)` as `asyncio.create_task` parallel to the formatter LLM; `await` it just before `finalize()` | Selector LLM (~1-2s on Haiku) is fully hidden behind the formatter LLM (~3-5s). Rebalancing's existing on-critical-path picker is removed |
| Selector failure mode | Empty list on timeout / API error / no-key (existing behavior in `chart_selector_service`) | Chart-less response, no user-visible error. Add a 3s soft ceiling — if the formatter finishes first and the selector is still running, cancel it and ship the response without charts rather than block |
| Chart names | Flat: `current_donut`, `category_gap_bar`, etc. — no domain prefix | Folder name = chart name = registry key; mild ambiguity is resolved by the rich description the selector LLM sees |
| Folder structure | Flat per-chart under `visualization_tools/<name>/{schema.py, builder.py, tests/}` (no domain grouping) | User preference for one-place catalog; matches the flat-name convention |
| Frontend mirror | `src/components/visualization_tools/<PascalCase>/{Chart.tsx, types.ts}` + dispatcher `ChartRenderer.tsx` | One folder per chart on each side; renderer keys off `payload.type` |
| `ChartBase` location | Stays in `_base.py` at the top of `visualization_tools/` | Shared `schema_version`, `title`, `subtitle` fields |
| `ChartPayload` discriminated union | Built in `registry.py` from imported per-chart payloads | The registry is the only place that imports every chart |
| Builder signature | `(db: AsyncSession, user_id: UUID, **kwargs) -> ChartPayload \| None` for AA / risk / performance / concentration; `(response: RebalancingComputeResponse) -> ChartPayload \| None` for rebalancing | AA charts read DB state; rebalancing charts shape in-memory engine output. The registry stores the builder callable; the chat-side caller passes the right kwargs per intent |
| Retired chart handling | `sub_asset_breakdown.py` and the old `asset_allocation/` folder move to `visualization_tools/archive/` | Memory rule: reversible deletes in non-git directories — never `rm -rf` |
| Visual direction | Editorial wealth (option II from visual brainstorm) | Reads "advisor", uses website's actual `wealth-*` tokens, matches existing `wealth-card` shadow + radius |
| Color semantics — asset classes | Equity = `wealth-blue`, Debt = `wealth-navy`, Real Estate = `wealth-green`, Cash = `wealth-amber` | Already established in `dashboard/AllocationChart.tsx` — keep it consistent |
| Color semantics — rebalancing series | Current = `muted-foreground` (gray), Target = `wealth-navy`, Plan = `wealth-blue` | Subdued "where you are", bold "where you should be", actionable "what to do" |
| Color semantics — directional | Buy = `wealth-green`, Sell = `destructive`; Top performers = `wealth-green`, Bottom = `destructive` | Universal red/green direction; matches the `--destructive` token already in the theme |
| Catalog visibility | (a) per-chart folders for cohesion, (b) `scripts/regen_chart_docs.py` writes `docs/charts.md` from `CHART_TOOLS` on demand | One-folder cohesion solves "scattered" pain; on-demand markdown solves "what charts exist" without a CI hook |
| Test placement | `<chart>/tests/test_builder.py` per chart; runs under existing `pytest app/services/visualization_tools/` | Keeps test ownership co-located with the chart; one-folder rule applies to tests too |

## The 9-chart launch catalog

| Name | Domain | Status | Builder input | What it shows |
|---|---|---|---|---|
| `current_donut` | Allocation | Relocate (existing builder) | `(db, user_id)` | Donut of asset-class mix with total ₹ at center |
| `concentration_risk` | Allocation | Relocate (existing builder) | `(db, user_id)` | Top-5 holdings + rest bar, severity badge (ok/watch/act) |
| `target_vs_actual` | Allocation | Relocate (existing builder) | `(db, user_id)` | Paired bars per asset class with drift labels; reads latest `AllocationRecommendation` |
| `top_bottom_funds` | Performance | NEW | `(db, user_id)` | Top-3 + bottom-3 funds by XIRR, with portfolio-average reference line |
| `profile_dial` | Risk | NEW | `(db, user_id)` | Conservative ↔ Aggressive gauge with the user's marker (reads from effective risk profile) |
| `category_gap_bar` | Rebalancing | Migrate from `ai_bridge/rebalancing/charts.py` | `(response)` | Current / Target / Plan grouped bars per SEBI sub-category |
| `planned_donut` | Rebalancing | Migrate | `(response)` | Donut of post-rebalance allocation share by sub-category |
| `tax_cost_bar` | Rebalancing | Migrate | `(response)` | STCG + LTCG + exit load per category, with totals |
| `buy_sell_ledger` | Rebalancing | NEW | `(response)` | Per-fund table: ₹ to buy / ₹ to sell, sorted by absolute trade size |

`sub_asset_treemap` is **retired** — moved to `visualization_tools/archive/`. Frontend renderer keeps its case removed; backend payload schema removed from the union.

## Architecture & wiring

### Selector — single Haiku call shared across both intents

`chart_selector_service.select_charts(question, intent) -> list[str]` already exists with the right shape; today it is dead code. After this spec it is the only chart selector in the codebase. The `ai_bridge/rebalancing/chart_picker.py` is **deleted** along with `_DETECT_SYSTEM`, `_summarise`, and `_CHART_TRIGGERS`.

The selector LLM sees only:
- the user's question,
- the classifier's intent,
- the live catalog (chart name + 1-paragraph description from `CHART_TOOLS`).

It returns chart names. No data dependency, so it parallelizes cleanly.

### Wiring inside `chat_core/brain.py`

Both AA and Rebalancing branches change shape from "engine → formatter → response" to "engine → (formatter ‖ chart selector) → builder → response":

```python
# AA branch (sketch)
selector_task = asyncio.create_task(select_charts(turn.user_question, intent_value))
result = await dispatch_chat(intent_value, turn_context)        # runs formatter LLM
chart_names = await asyncio.wait_for(selector_task, timeout=3)  # already done in practice
chart_payloads = [
    p.model_dump(mode="json")
    for p in await build_charts_for_aa(db, uid, chart_names)    # cheap DB selects
    if p is not None
]
return await finalize(result.text, chart_payloads=chart_payloads or None, ...)
```

Rebalancing differs in two places: (1) selector must wait until *after* the engine has run because the engine output drives the builders, and (2) the builders take `RebalancingComputeResponse` directly (no DB hit). Otherwise identical.

The build step lives in two thin functions, one per intent:
- `visualization_tools/build_aa.py :: build_charts_for_aa(db, user_id, chart_names) -> list[ChartPayload]`
- `visualization_tools/build_rebalancing.py :: build_charts_for_rebalancing(response, chart_names) -> list[ChartPayload]`

These do the dispatch from name → builder for the names the selector returned. They live next to `registry.py`, not inside any one chart's folder.

### Timing model (recap)

```
AA today:        [intent] [engine] [formatter]                     ≈ 4-5s, 0 charts
AA proposed:     [intent] [engine] [formatter ‖ selector] [build]  ≈ 4-5s, charts
Rebal today:     [intent] [engine] [formatter] [picker]            ≈ 6-9s, charts
Rebal proposed:  [intent] [engine] [formatter ‖ selector] [build]  ≈ 5-6s, charts
```

Selector LLM call (~1-2s on Haiku) is fully hidden behind the formatter (~3-5s). Builders are ~30-100ms (DB selects for AA) or ~5ms (in-memory for Rebal). Net: AA gets charts for free wall-clock; Rebal saves 1-2s.

## File layout

### Backend — `app/services/visualization_tools/`

```
visualization_tools/
  _base.py                       # ChartBase, SCHEMA_VERSION (shared)
  registry.py                    # imports each chart, builds CHART_TOOLS dict + ChartPayload Union
  build_aa.py                    # build_charts_for_aa(db, user_id, names)
  build_rebalancing.py           # build_charts_for_rebalancing(response, names)
  current_donut/        schema.py, builder.py, tests/
  concentration_risk/   schema.py, builder.py, tests/
  target_vs_actual/     schema.py, builder.py, tests/
  top_bottom_funds/     schema.py, builder.py, tests/    # NEW
  profile_dial/         schema.py, builder.py, tests/    # NEW
  category_gap_bar/     schema.py, builder.py, tests/    # migrated
  planned_donut/        schema.py, builder.py, tests/    # migrated
  tax_cost_bar/         schema.py, builder.py, tests/    # migrated
  buy_sell_ledger/      schema.py, builder.py, tests/    # NEW
  archive/                       # old asset_allocation/ folder + sub_asset_breakdown.py move here
```

`ai_bridge/rebalancing/charts.py` and `ai_bridge/rebalancing/chart_picker.py` are deleted (their tests too); `ai_bridge/rebalancing/chat.py` stops attaching `outcome.chart` and instead produces a chart-free outcome — chart wiring moves to `brain.py`.

### Frontend — `src/components/visualization_tools/`

```
visualization_tools/
  ChartRenderer.tsx              # dispatcher, switch on payload.type
  _base.ts                       # ChartBase TS shape, ChartPayload union type
  CurrentDonut/         Chart.tsx, types.ts
  ConcentrationRisk/    Chart.tsx, types.ts
  TargetVsActual/       Chart.tsx, types.ts
  TopBottomFunds/       Chart.tsx, types.ts              # NEW
  ProfileDial/          Chart.tsx, types.ts              # NEW
  CategoryGapBar/       Chart.tsx, types.ts              # migrated from rebalancing/
  PlannedDonut/         Chart.tsx, types.ts              # migrated
  TaxCostBar/           Chart.tsx, types.ts              # migrated
  BuySellLedger/        Chart.tsx, types.ts              # NEW
  _archive/                      # existing archive/ folder, plus retired components
```

The existing `rebalancing/` subfolder under `visualization_tools/` is removed; its three components are rewritten flat into the new per-chart folders with the editorial-wealth styling. `types.ts` and `index.ts` at the top of `visualization_tools/` are updated to import from the new locations.

## Visual design system

Applied uniformly across all 9 charts. Frontend tokens come from `tailwind.config.ts` (`wealth-navy`, `wealth-blue`, `wealth-green`, `wealth-amber`, etc.) and `index.css` (`Instrument Serif` for display, `DM Sans` for body).

| Element | Spec |
|---|---|
| Card | `bg-card`, `rounded-2xl`, `shadow-wealth`, `p-5`, border `border-border` |
| Title | `Instrument Serif` italic, `text-foreground`, ~`text-xl` (chat-embedded uses `text-base`) |
| Subtitle / caption | `DM Sans`, `text-xs`, `text-muted-foreground`, `mb-4` |
| Body text | `DM Sans` |
| Asset-class palette | Equity = `wealth-blue`, Debt = `wealth-navy`, Real Estate = `wealth-green`, Cash = `wealth-amber` |
| Rebalancing series | Current = `muted-foreground`, Target = `wealth-navy`, Plan = `wealth-blue` |
| Directional | Buy / top-performers = `wealth-green`; Sell / bottom-performers = `destructive` |
| Severity (concentration) | ok = `wealth-green`, watch = `wealth-amber`, act = `destructive`. Pill: `text-xs` `font-semibold` `rounded-full` `px-2 py-0.5`, light-tint background of the same hue |
| Risk dial | Background gradient from `wealth-green-light` (Conservative) → `wealth-blue-light` (Balanced) → `wealth-amber-light` (Aggressive); needle `wealth-navy` |
| Donut center label | `text-[10px] uppercase tracking-wide opacity-60` for "Total"; `text-lg font-bold` for the ₹ value |
| Legend rows | `border-b border-dashed border-border/60`, `py-2`, swatch `w-2.5 h-2.5 rounded` |
| Bar radius | Bars `rounded-r-md` (vertical layout) — soft, not pill-shaped |

Chart components keep using Recharts, with `<ResponsiveContainer>` driving width and a fixed `chartHeight` per chart type. All hex values are removed and replaced with `hsl(var(--wealth-...))`.

## Catalog visibility — `docs/charts.md`

A new dev script generates the markdown reference:

`scripts/regen_chart_docs.py` walks `CHART_TOOLS`, prints one section per chart with:
- name (anchor)
- selector description (the one the LLM sees)
- expected payload shape (Pydantic JSON schema, pretty-printed)
- frontend renderer file path

Output: `docs/charts.md`, committed alongside the code that adds/changes charts. **Not** triggered by a pre-commit hook — running it is a developer step. README mentions the command.

## Migration plan

Order matters for safety in a non-git working dir (memory rule: reversible deletes only).

1. **Add new infrastructure side-by-side.** Create `_base.py`, refactor `registry.py` to import from chart folders (initially empty). No behavior change yet.
2. **Per-chart relocate, in this order, each in its own commit:**
   - `current_donut`
   - `concentration_risk`
   - `target_vs_actual`
   Backend: move builder + split schema into the new folder; keep old `asset_allocation/` files importing from new locations during transition; run `pytest app/services/visualization_tools/` per step.
3. **Migrate rebalancing charts.** For each of `category_gap_bar`, `planned_donut`, `tax_cost_bar`: write a typed Pydantic schema (replacing the dict-shape `ChartSpec`), port the computer logic to a builder taking `RebalancingComputeResponse`, register it, write tests. Frontend Chart components rewritten in the new style and folder.
4. **Build new charts.** `top_bottom_funds`, `profile_dial`, `buy_sell_ledger` — schema + builder + frontend Chart + tests, in three commits.
5. **Wire selector + builders into `brain.py`.** Replace AA branch's plain `dispatch_chat` call with the parallel-selector pattern; replace rebalancing branch's `result.chart` plumbing with the same pattern. Add the 3s soft timeout.
6. **Remove dead paths.** Delete `ai_bridge/rebalancing/chart_picker.py`, `chart_picker` tests, `ai_bridge/rebalancing/charts.py`, the chart-related code in `ai_bridge/rebalancing/chat.py`, and the now-empty old AA folder. Move retired files to `visualization_tools/archive/` (and frontend equivalents to `_archive/`) — don't `rm`.
7. **Generate `docs/charts.md`.** Run `scripts/regen_chart_docs.py`; commit alongside the code.
8. **Visual polish pass.** Walk all 9 frontend Chart components, replace hex literals with `hsl(var(--wealth-...))`, swap titles to `Instrument Serif` italic, verify mobile container width (`max-w-md`).

Each step is commit-sized and independently testable. Steps 2-4 can be parallelized across PRs if desired; steps 5-8 are serial.

## Testing

| Layer | What to test | Where |
|---|---|---|
| Builder unit tests | Each builder: happy path with seed data, empty/None portfolio, edge cases (one holding, all-zero values) | `<chart>/tests/test_builder.py` |
| Registry test | `CHART_TOOLS` round-trip: every name → builder → produces a payload that validates against its schema, given seed data | `visualization_tools/tests/test_registry_smoke.py` |
| Selector contract test | `select_charts()` against a fixed catalog: returns names that exist in the registry; returns `[]` on no-API-key (without calling Anthropic) | `ai_bridge/tests/test_chart_selector_service.py` (unit-level, mocked LLM response) |
| Selector boundary eval (live LLM) | 8-12 question/expected-charts cases for AA + Rebalancing, gated on `ANTHROPIC_API_KEY`. Threshold-based, like `test_intent_classifier_boundary_evals.py` | `AI_Agents/tests/test_chart_selector_boundary_evals.py` (uses the upcoming shared eval harness once it lands) |
| Brain integration | One AA-intent test asserts `chart_payloads` is non-empty in the `ChatBrainResult`; one rebalancing-intent test the same. Mock the formatter + selector | `chat_core/tests/test_brain_charts.py` |
| Frontend | Storybook stories per chart with a representative payload; Vitest snapshot tests on the renderer dispatch | `src/components/visualization_tools/__tests__/` |
| Visual regression | Out of scope — manual review during the polish pass (step 8) |  |

## Open questions / things to confirm in implementation

- Risk-dial source data: does `effective_risk_profile` already expose a 0-100 score we can read directly, or do we need a small mapper? (Leaning: read existing score; build the dial as a 5-band gauge.)
- `top_bottom_funds`: XIRR per holding — is there a precomputed field on `PortfolioHolding`, or do we compute it from transactions? If the latter and it's slow, the builder may need a CTE rather than a Python aggregation.
- `buy_sell_ledger` design: list view (one row per fund) vs grouped by sub-category. Default = list view with subtle sub-category headers, tightest rendering for chat.
- Whether the auto-doc generator should include sample JSON payloads (good for frontend devs) or only schema (smaller, less noise). Lean toward schema-only for now; sample payloads added if requested.

These are surface-area details that don't change the architecture and can be settled when each chart's builder is written.
