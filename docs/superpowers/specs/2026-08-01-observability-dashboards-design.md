# Backend observability: failure tracking + PostHog dashboards

**Date:** 2026-08-01
**Status:** design approved, not implemented
**Depends on:** branch `Backend_Observablity` (2 commits, unpushed) — scanner-noise filtering,
`DEPLOY_ENV`/`GIT_COMMIT` labelling, SQLAlchemy + httpx spans, job spans, infra metrics.

## Problem

Two dashboards exist today ("client errors 4xx", "backend errors 5xx"). Both are built on HTTP
status codes, and both are effectively empty. They are not broken — they are measuring a layer
where this application does not express failure.

Every AI flow enters through one endpoint: `POST /chat/sessions/{id}/messages` →
`ChatBrain.run_turn` → `FLOWS[intent]`. There is **no** `POST` endpoint that runs a rebalancing;
`app/domains/rebalancing/routers/rebalancing_router.py` is read-only (list / readiness / detail /
update-status). And `ChatBrain` catches everything:

- flow timeout → apology text → **HTTP 201** (`services/brain.py:268`)
- any exception → rollback → apology text → **HTTP 201** (`services/brain.py:313`)

So seven distinct business operations — asset allocation, rebalancing, portfolio query, goal
planning, market query, additional investment, general chat — are one indistinguishable row in
PostHog, always succeeding. A user watching rebalancing fail and a user getting a perfect plan
produce byte-identical telemetry.

Separately, 74 call sites log an exception with a full stack trace (58 `logger.exception` + 16
`exc_info=True`). Exactly 2 reach PostHog Error Tracking. The other 72 produce a log line that
expires in ~14 days and cannot be counted, charted, or grouped.

### Measured, not argued

`http_request` went live 2026-07-28. Queried 2026-08-01 via the PostHog connector:

| Day | 2xx | 4xx | 5xx |
|---|---|---|---|
| Jul 28 | 22 | 5,388 | **0** |
| Jul 29 | 1,627 | 14,875 | **0** |
| Jul 30 | 117 | 16,899 | **0** |
| Jul 31 | 334 | 15,235 | **0** |
| Aug 1 | 25 | 8,855 | **0** |

**63,377 requests, zero 5xx, five days.** In that same window the mfapi job died on a NUMERIC
overflow and left an advisory lock stuck — neither produced a single durable event. 4xx is 96.6%
of the total (scanner noise; the fix is on the undeployed branch). Real served traffic is ~425/day,
so event volume is a non-issue at this stage.

Exactly one real backend `$exception` exists in 30 days: an `IntegrityError` / `UniqueViolationError`
on `POST /api/v1/goals/` (2026-07-27), correctly carrying `error_type`, `path`, and `service`.

## Decisions

| # | Decision |
|---|---|
| 1 | Code changes and dashboards ship together, not dashboards alone |
| 2 | Flow outcome is **binary** (`ok` / `failed`) plus a `failure_reason` string — not a three-state `degraded` |
| 3 | Swallowed exceptions reach Error Tracking via **one automatic logging handler**, not hand-instrumented call sites |
| 4 | **Two** dashboards, split by question: product health vs machine health |
| 5 | The 5xx dashboard is **kept and extended**; the 4xx dashboard's tiles are **rewritten in place** onto `http_request` (3 dropped as unfixable). Revised 2026-08-01 from "archive both" once the tiles were actually read — see Part 2. |
| 6 | `flow_completed` **carries the user id**, so support can answer "did this customer hit it" |
| 7 | Dashboards are built through the **PostHog MCP connector**, already authenticated to project 484367 — no personal API key needed. Every query is verified against real data before it becomes a tile. |
| 8 | The event-level `service` property is renamed **`domain`** — it currently collides with a super property and is silently discarded (§1.5) |
| 9 | `job_completed` is emitted **by `@traced_job` itself**, not hand-written per job, so new jobs are covered by adding one decorator |
| 10 | A **comment-trimming pass** on the five observability files, in its own commit (§1.6) |

## Part 1 — Code changes

### 1.1 New event: `flow_completed`

Emitted from `ChatBrain._finalize` (`app/domains/ai_engine/services/brain.py:359`). All four exit
paths already funnel through it, so no new control flow is introduced — `_finalize` gains two
keyword arguments and the four call sites pass them.

| Call site | outcome | failure_reason |
|---|---|---|
| `brain.py:246` canned short-circuit (out-of-scope) | `ok` | — |
| `brain.py:289` flow timeout | `failed` | `timeout` |
| `brain.py:301` normal flow result | `ok` | — |
| `brain.py:332` exception | `failed` | exception class name, or `llm_auth_failure` |

Properties:

```
flow_completed
  intent          str    "rebalancing"
  outcome         str    "ok" | "failed"
  failure_reason  str?   null | "timeout" | "llm_auth_failure" | "<ExceptionClass>"
  duration_ms     int
  flow            str    "flow_rebalancing"
  distinct_id     the user (decision 6)
```

`failure_reason` carries the exception **class name only** — never `str(exc)`. Exception messages
embed user data (the same reason `exceptions.py` logs field names, not values, for
`ValidationError`), and this is a 12-month analytics property.

**Coverage boundary.** 6 of 8 module services contain zero `except` blocks, so genuine failures
propagate to the brain and are caught correctly. Only `general_chat` (2 handlers) and
`market_commentary` (3) degrade in-module and would record `ok` despite an internal failure —
the two lowest-stakes flows. Those are covered by Error Tracking (1.2) instead. An `error` field
on `ModuleOutput` was considered and rejected as unnecessary given this distribution.

### 1.2 Automatic exception → Error Tracking

New module in `app/core/`. A `logging.Handler` whose `emit` forwards any record carrying
`exc_info` to `capture_exception`, attached to the same targets as `attach_otel_logging`
(`app/core/otel.py:310`) — root `""` and `uvicorn`, **not** `uvicorn.error`.

Four properties it must have:

1. **Dedupe moves into `capture_exception`** (`app/core/observability.py:119`), reusing the
   `_REPORTED_FLAG` mechanism from `app/core/job_tracing.py`. Today `exceptions.py:96`, the job
   reporter, and this new handler could each file the same exception. One choke point fixes all
   three pairings at once.
2. **Throttle**, keyed by `(logger name, exception class, filename:lineno)`, max 1 report per 60s
   per key. Required, not optional: `networth_history_service.py:367` logs an exception **per
   scheme** inside a backfill loop, so a single mfapi outage would otherwise emit thousands of
   Error Tracking events in one run. The log line still ships to Logs regardless, and the job's
   aggregate failure count still lands on `job_completed` — the throttle drops duplicate *issue
   reports*, not information.
3. **Reentrancy guard** (ContextVar), so a failure inside PostHog's own SDK cannot recurse.
4. **Env flag** `POSTHOG_ERROR_CAPTURE_ENABLED`, default on, so one `pm2 reload --update-env`
   disables it without a deploy.

Records without `exc_info` are ignored — there is no exception object to group on. That excludes
bare `logger.error("...")` sites by design.

**Level: WARNING and above**, matching `attach_otel_logging`. Not ERROR-only. The deciding case is
`app/domains/mutual_funds/services/mfapi_scheduler.py:214` —
`logger.warning("failed to release advisory lock", exc_info=True)`. A stuck mfapi advisory lock is
a live open bug in this codebase, and it is logged at WARNING; an ERROR-only handler would not have
caught it. The throttle (point 2) is what makes the wider level safe.

### 1.3 New event: `job_completed`

Emitted **by the `@traced_job` decorator itself** on both the success and failure paths — not
hand-written in each job. This is the only item on the requirement list that nothing currently
covers, and decorator-emission is what stops it rotting: a future job gets full tracking by adding
one decorator, with nobody needing to remember a second step. Per-job counts are contributed by the
job through the yielded span/context rather than duplicated at each call site.

```
job_completed
  job             str    "mfapi.daily_job" | "networth.daily_job" | "benchmark.refresh_job"
  outcome         str    "ok" | "failed"
  failure_reason  str?   exception class name
  duration_ms     int
  <per-job counts>       nav_rows_inserted, failed_codes, users_processed, users_failed
```

**Why an event rather than a span.** A job that stops running entirely emits no error, no span,
and no log — there is nothing to find. It is detectable only as the *absence* of an expected
event, and absence can only be charted against a durable signal. Spans expire in ~14 days, which
also makes "did the NAV job run every day last quarter?" unanswerable.

### 1.4 Instrument `run_daily_networth_job`

`app/domains/portfolio/services/networth_history_service.py:756` — the third scheduled job, and
the only one that touches every user's money. Currently has no `@traced_job`, no
`report_job_failure`, and no `job_completed`.

Its per-user handler at `:793` (*"never let one user block the rest"*) means that today, if 40 of
50 users fail their net-worth calculation, the job logs 40 tracebacks and reports success. After:
one grouped Error Tracking issue with 40 occurrences, plus `users_failed=40` on the event.

### 1.5 Rename the event-level `service` property to `domain`

**A live bug on deployed `main`, not a new feature.** `capture_http_request` computes
`_service_from_path(path)` (`app/core/exceptions.py:136`) — `/api/v1/goals/` → `goals` — and passes
it as the `service` property. It is then silently discarded, because `posthog/client.py:1666` is:

```python
msg["properties"] = {**msg["properties"], **self.super_properties}
```

Super properties merge **second** and overwrite same-named event properties. `super_properties`
carries `service: "prozpr-backend"` (`observability.py:83`), so every event's domain value is
overwritten. Measured 2026-08-01:

| Day | `service` | `environment` | n |
|---|---|---|---|
| Jul 27 | **`goals`** | *(none)* | 1 |
| Jul 27 | `prozpr-backend` | development | 3 |
| Jul 28 → Aug 1 | `prozpr-backend` | development | 63,595 |

That lone `goals` row predates the super-properties deploy and is the only evidence the feature
ever worked. Since then: one bucket, 63,595 events, zero domain visibility.

**Fix — two axes that collided on one word:**

- `service` stays `"prozpr-backend"` — *which deployable*. Correct today: one PM2 process, one box.
  When the backend is genuinely split later, this varies on its own and needs no redesign.
- `domain` (new name for the event property) — *which business area*: `goals`, `chat`, `portfolio`,
  `rebalancing`. Matches the repo's own `app/domains/` vocabulary.

Applies to `capture_http_request` and `capture_exception`'s caller in `exceptions.py`. One existing
tile — *Server errors by service (30d)* on dashboard `1904646` — queries `properties.service` and
must move to `properties.domain`; it is on a dashboard already being edited.

**Why this is load-bearing for scale.** Left broken, every domain added from here collapses into
the same undifferentiated bucket, so the dashboards get *less* informative as the product grows.

### 1.6 Comment budget

Measured 2026-08-01 across 234 files in `app/`: the codebase averages **14.8%** comment +
docstring lines (median file 13.6%). The five files this work touched:

| File | prose % |
|---|---|
| `job_tracing.py` | 47.1% |
| `log_scrubber.py` | 46.5% |
| `observability.py` | 38.2% |
| `otel.py` | 36.6% |
| `exceptions.py` | 32.3% |

Two to three times the norm. A separate commit trims them to **~20%** — above baseline, because
infra code earns more explanation than a CRUD service, but not triple.

**Keep** a comment only if deleting it costs a debugging session: a landmine (looks wrong, is
deliberate, changing it silently breaks something), a number with a source (*why* `kill_timeout`
is 8000), or a documented dead end. **Delete** anything restating the line below it, and anything
already recorded in `app/core/CLAUDE.md` — that duplication is the bulk of the excess, and
CLAUDE.md is the better home because it is what a new session reads first.

Not a repo-wide campaign: `app/domains/ai_engine/types.py` (65.3%) and `app/main.py` (54.2%) are
higher still and are out of scope. This pass covers only the files this work touched.

### 1.7 Explicitly not adding

Four of the six requested areas are already covered; new signals would be noise.

- **DB queries** — SQLAlchemy spans give per-query timing and errors for 14 days. A DB outage is
  already a 503 in `http_request` and an exception in Error Tracking.
- **Outbound HTTP** — httpx spans for detail, exceptions for failures, per-run aggregates on
  `job_completed`.
- **LLM calls** — `$ai_generation` already carries model, tokens, latency, cost, and errors.
- **Infra / CPU / memory / disk** — the OTLP metrics on the `Backend_Observablity` branch.

### 1.8 Volume

`flow_completed` is one event per chat turn; `job_completed` is ~9–21/day. Both are negligible
against the 1M/month analytics allowance (currently ~3% after the scanner fix). Error Tracking
steady state should be single digits per day; the throttle bounds the pathological case and the
env flag is the backstop.

## Part 2 — Dashboards

### Dashboard 1: "Is chat performing as expected?"

New dashboard. Source: `flow_completed`. The daily glance.

| Tile | Answers |
|---|---|
| Flow success rate, 7d, vs prior period | The one number |
| Failures by `intent` (bar) | *Which* thing is broken |
| Failures by `failure_reason` (bar) | *Why* |
| Outcome over time (stacked, daily) | Did a deploy break it — compare against `service_version` |
| Flow latency p95 by intent | Timeouts have a warning shape before they become failures |
| Unique users affected, 7d | One user or forty |
| Chat volume, daily | The denominator |

The intent → reason pair is the core of it: what broke, then why, in two clicks.

**No LLM tiles.** Dashboard `1904596` ("LLM Cost, Speed & Reliability") already carries ~15 built
insights — cost by model, cost per turn, cost by intent, total spend, reliability. Link to it; do
not duplicate. One repair needed there: its *LLM failures over time (30d)* tile queries the dead
`http_client_error` event and needs the same rewrite as the 4xx tiles below.

### Dashboard 2: "Is the machine healthy?"

Built by extending the existing dashboard `1904646`, whose 5 `$exception` tiles are kept as-is.
Sources: `$exception`, `http_request`, `job_completed`, `posthog.metrics`. The investigation board.

| Tile | Answers |
|---|---|
| 5xx rate + sparkline | True server errors |
| 4xx on served routes | Meaningful now that scanner noise is filtered |
| Errors by endpoint (table) | Which endpoint is angry |
| API latency p95 by endpoint (top 10) | What is slow |
| **Job runs, 7d, by job × outcome** | **Absence check** — mfapi runs 3×/day, so 7d = 21. A count of 14 means it silently stopped. |
| Job duration trend | A job creeping toward its window is a future outage |
| Job data volume (`nav_rows_inserted`, `users_failed`) | A job that "succeeds" while inserting 0 rows |
| CPU / memory / disk | Infra — verified queryable; see the aggregation caveats under Risks |
| Request volume | Context |

Top exceptions gets a **link to PostHog Error Tracking**, not rebuilt tiles. That page already
groups by root cause with stack traces and occurrence counts; insights would be strictly worse.

### Existing dashboards

Read tile-by-tile on 2026-08-01. The two boards are in completely different states, and the earlier
"archive both" call was wrong.

**`1904646` "Backend Errors (5xx)" — KEEP and extend.** All 5 tiles query `$exception` filtered to
`$lib = 'posthog-python'`. That filter is correct, the properties they project (`error_type`, `path`,
`service`) are really emitted by `exceptions.py`, and the one real backend exception on record
renders correctly. These tiles are **starved, not wrong**. This dashboard becomes dashboard 2; the
job, latency, and infra tiles are added to it.

One edit inside it: *Server errors by service (30d)* must move from `properties.service` to
`properties.domain` (§1.5), otherwise it groups every error under the single value
`prozpr-backend`.

**`1904647` "Client Errors (4xx)" — REWRITE in place.** All 10 tiles query `http_client_error`:
5 events ever, dead since 2026-07-26. Seven are sound queries needing only a source swap to
`http_request` + `status_class = '4xx'` — by status code, by endpoint, by endpoint × status, by
method, week-over-week, auth failures (401/403), errors over time. Rewriting preserves the existing
layout and is cheaper than rebuilding.

Three are **deleted**, not repaired:

- *Client errors by user* and *New vs recurring client errors* — both key on `distinct_id`, which
  `capture_http_request` hardcodes to `"backend"` with `$process_person_profile: False`. Per-user
  math over a single synthetic identity is meaningless.
- *Client error rate vs pageviews* — used `$pageview` as a proxy denominator because the old event
  only fired on errors. `http_request` records every request, so this is rebuilt as a true 4xx rate
  rather than repaired.

**Check before rewriting:** several of the newer insights set `filterTestAccounts: true`. Confirm
that filter does not exclude `distinct_id = "backend"`, or every rewritten tile reads zero for a
new reason.

## Risks and open items

- **~~Infra tile is unverified.~~ RESOLVED 2026-08-01.** `posthog.metrics` is queryable via HogQL
  and the metrics are arriving: `system.cpu.utilization`, `system.memory.usage`,
  `system.memory.utilization`, `process.memory.usage`, `process.memory.virtual`, and the
  hand-rolled `system.filesystem.utilization`, all under `service_name = 'prozpr-backend'`. No
  `infra_snapshot` fallback needed. Two things the tile queries must respect:
  - `system.cpu.utilization` is **per-core** (288 points where memory has 16) — aggregate across
    the `cpu` attribute or the tile shows 12 overlapping series.
  - `process.memory.*` are `cumulative` sums, not gauges. Use `argMax(value, timestamp)`;
    `SUM(value)` double-counts. Gauges have empty `aggregation_temporality`.
  - `process.runtime.cpython.memory` also arrives and is not in `_SYSTEM_METRICS` — an instrumentor
    default. Harmless, but do not assume the config is the full list of what lands.
- **Absence detection is a chart, not an alarm.** The job-runs tile shows a wrong number; it will
  not page anyone. Alerts are explicitly out of scope for this round.
- **`app/core/tests/` is gitignored** (`.gitignore:121`). Tests written for this work cannot be
  committed, exactly as with the current branch. Un-ignoring that path is a separate decision.
- **Nothing is deployed.** `Backend_Observablity` is 2 commits ahead of origin and `deploy.yml`
  only fires on `main`. This work stacks on top of an undeployed branch.

## Verification

- Unit tests for `_finalize` outcome mapping across all four exit paths, the throttle, the dedupe
  choke point, and the reentrancy guard.
- **A test that `domain` survives the super-properties merge** — the bug in §1.5 was silent for five
  days and 63,595 events. Assert on the payload the SDK would send, not on the call arguments, or
  the test reproduces the same blind spot it exists to prevent.
- A test that `@traced_job` emits `job_completed` on **both** the success and failure paths — the
  decorator is the only thing standing between a future job and no coverage.
- Extend `scratchpad/preflight_telemetry.py` (the existing `DEPLOY_ENV=preflight*` harness that
  skips the lifespan so it never touches the live RDS) to assert `flow_completed` and
  `job_completed` arrive, and that one exception files exactly one Error Tracking issue.
- Baseline the test suite before and after; CI runs no tests, so this must be measured locally.
- Re-run the comment-density script after the §1.6 trim to confirm the five files land near 20%.
- Every dashboard query verified through the connector against real returned data, not assumed.
  Confirm `filterTestAccounts: true` does not exclude `distinct_id = "backend"` before rewriting
  any tile.
