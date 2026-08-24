# Prozpr Backend — Architecture

FastAPI · SQLAlchemy async · PostgreSQL · Claude via LangChain.
Pick a lane below; each opens to its own diagram.

---

## 1. The whole system

```mermaid
flowchart LR
    FE["Prozpr_Frontend<br/>React SPA"] -->|HTTPS /api/v1| NG[nginx]
    NG --> UV["uvicorn · app.main:app"]

    subgraph APP["app/"]
      direction TB
      R["routers/<br/>all_routers"] --> D["domains/ · 22"]
      D --> C["core/<br/>config · db · auth · otel"]
    end

    UV --> APP
    D --> PG[("PostgreSQL")]
    D --> AI["AI_Agents/src<br/>sys.path injected"]
    AI --> AN["Anthropic Claude<br/>via LangChain"]
    D --> EXT["mfapi.in · casparser.in<br/>SimBanks · Fintech Primitives<br/>Resend · Zoom"]
    C --> PH["PostHog<br/>events + OTLP spans"]
```

---

## 2. Layers — where code is allowed to live

```mermaid
flowchart TD
    A["app/routers/__init__.py<br/><i>all_routers — mount order</i>"]
    B["domains/&lt;x&gt;/routers/*_router.py<br/><i>HTTP only: status codes, schemas</i>"]
    C["domains/&lt;x&gt;/schemas/<br/><i>pydantic request/response</i>"]
    D["domains/&lt;x&gt;/services/*_service.py<br/><i>business logic</i>"]
    E["domains/&lt;x&gt;/models/<br/><i>SQLAlchemy ORM</i>"]
    F["core/<br/><i>config · database · dependencies · security · otel</i>"]
    G["AI_Agents/src/&lt;agent&gt;<br/><i>pydantic in → pydantic out</i>"]

    A --> B --> C
    B --> D
    D --> E
    D --> G
    B -.-> F
    D -.-> F
    E -.-> F

    classDef rule fill:#fff3cd,stroke:#d39e00,color:#000
    class G rule
```

**Two rules that keep it flat**

| Rule | Consequence |
| --- | --- |
| A domain never imports another domain | cross-domain data travels via a flow's `prior` dict, never a reach-in |
| Only the owning `*_module_service.py` may import its `AI_Agents` orchestrator | one gateway per agent; see [AI_MODULES.md](./AI_MODULES.md) |

---

## 3. Pick an entry point

<details>
<summary><b>▸ A. Authenticated HTTP request</b> — the default path for every REST call</summary>

```mermaid
sequenceDiagram
    autonumber
    participant Cl as Client
    participant FA as FastAPI
    participant Dep as core/dependencies
    participant Rt as domains/*/routers
    participant Sv as domains/*/services
    participant DB as PostgreSQL
    participant Ob as core/observability

    Cl->>FA: request with a bearer JWT
    FA->>Dep: get_current_user
    Dep-->>FA: User (JWT decoded)
    opt X-Family-Member-Id present
        FA->>Dep: get_effective_user
        Dep-->>FA: family-member User
    end
    FA->>Rt: handler(user, db=get_db())
    Rt->>Sv: await service(...)
    Sv->>DB: await session.execute(...)
    DB-->>Sv: rows
    Sv-->>Rt: domain objects
    Rt-->>FA: pydantic response schema
    FA->>Ob: otel_response_hook
    Ob-->>Ob: http_request event + OTLP span
    FA-->>Cl: JSON
```

> `path` recorded is always the **route template**, never the raw URL — 58 of 222 routes are parameterised.
> Only paths under `API_V1_PREFIX` are observable; scanner traffic is dropped at the sampler.

</details>

<details>
<summary><b>▸ B. Chat turn</b> — the AI hot path</summary>

```mermaid
sequenceDiagram
    autonumber
    participant Cl as Client
    participant CR as chat_router
    participant CB as ChatBrain.run_turn
    participant IC as intent_classifier
    participant FL as FLOWS[intent]
    participant DM as owning domain(s)
    participant AF as answer_formatter
    participant DB as PostgreSQL

    Cl->>CR: POST /chat/sessions/{id}/messages
    CR->>CB: ChatTurnInput
    CB->>CB: build_turn_context (history, last runs, active intent)
    CB->>IC: always first
    IC-->>CB: IntentDecision + tools_needed

    alt out_of_scope / stock_advice
        CB-->>CR: canned or tailored redirect
    else no holdings imported
        CB-->>CR: "add your CAMS statement" + CTA flag
    else
        CB->>FL: flow(turn, ctx) under timeout
        FL->>DM: run(turn, ctx, prior)
        DM->>AF: facts pack + body prompt
        AF-->>DM: customer-facing text
        DM-->>FL: ModuleOutput
        FL-->>CB: final ModuleOutput
    end
    CB->>DB: _finalize — telemetry (best-effort)
    CB-->>CR: ChatBrainResult
    CR-->>Cl: assistant message
```

Full intent-by-intent breakdown → **[AI_MODULES.md](./AI_MODULES.md)**

</details>

<details>
<summary><b>▸ C. CAMS / CAS ingest</b> — how a portfolio gets into the system</summary>

```mermaid
flowchart TD
    U["User uploads CAS PDF<br/>or requests a mailback"] --> RT["ingestion/routers<br/>mf_ingest_router"]
    RT --> SZ{"file &gt; ~1.7 MB?"}
    SZ -->|yes| S3["stage in private S3<br/>presigned ~10 min"]
    SZ -->|no| MP["multipart upload"]
    S3 --> CP["casparser.in REST API"]
    MP --> CP
    CP --> AD["adapter → legacy dict"]
    AD --> VAL{"validation gates"}
    VAL -->|Summary-only / zero value| REJ["422 reject"]
    VAL -->|ok| WIPE["reset_user_financial_data<br/><i>full wipe, keeps profile/goals/chat</i>"]
    WIPE --> NRM["mf_aa_normalizer<br/>transaction-derived holdings"]
    NRM --> INS[("mf_transactions<br/>chunked 1500 rows")]
    INS --> BF["background net-worth backfill<br/><i>best-effort, never fails the upload</i>"]
```

</details>

<details>
<summary><b>▸ D. Background jobs & schedulers</b></summary>

```mermaid
flowchart LR
    LS["core/lifespan<br/>_start_schedulers()"] --> F1{"MFAPI_SCHEDULER_ENABLED"}
    LS --> F2{"BENCHMARK_SCHEDULER_ENABLED"}
    F1 -->|on| MF["mfapi daily sweep<br/>~8k schemes"]
    F2 -->|on| BM["benchmark refresh<br/>Nifty50 via NAV proxy"]

    MF --> SUP["suppress_instrumentation()<br/>per-item loop"]
    BM --> SUP
    SUP --> SPAN["keep run/phase spans only"]
    MF --> RPF["report_job_failure()<br/>in every except"]
    BM --> RPF

    classDef warn fill:#f8d7da,stroke:#c00,color:#000
    class SUP,RPF warn
```

> Both guards are load-bearing: without `suppress_instrumentation()` one sweep emits ~25k spans;
> without `report_job_failure()` a crashed job stays green (schedulers swallow their own exceptions).

</details>

<details>
<summary><b>▸ E. Observability — two pipelines, on purpose</b></summary>

```mermaid
flowchart LR
    APP["app/"] --> SP["OTLP spans + logs<br/>core/otel.py"]
    APP --> EV["PostHog events<br/>core/observability.py"]
    SP -->|"~14 day retention"| PH[(PostHog)]
    EV -->|"12 month retention"| PH
    APP --> SCR["log_scrubber<br/>phone · PAN · email redacted"]
    SCR --> SP
```

`http_request` and `$ai_generation` are **events**, not spans, because they need long history. Do not consolidate.

</details>

---

## 4. Domain map

```mermaid
flowchart TB
    subgraph IDN["identity & profile"]
      identity; profile; goals
    end
    subgraph PORT["portfolio & market data"]
      portfolio; mutual_funds; equities; benchmarks
    end
    subgraph ADVICE["advice engines"]
      asset_allocation; practical_asset_allocation
      rebalancing; cashflow; additional_investment
    end
    subgraph CHATG["chat"]
      ai_engine; chat; intent_classifier
      general_chat; market_commentary
    end
    subgraph OPS["io & ops"]
      ingestion; execution; notifications; advisory; support
    end

    IDN --> ADVICE
    PORT --> ADVICE
    ADVICE --> CHATG
    OPS --> PORT
```

Each domain carries **only** the sub-folders it needs — `general_chat/` and `market_commentary/` are services-only; `asset_allocation/` has no `routers/`. Check before assuming a layer exists.

---

## 5. Persistence & migrations

```mermaid
flowchart LR
    M["domains/*/models/*.py"] --> AM["app/all_models.py<br/><i>registers with Base.metadata</i>"]
    AM --> B["core/database.Base"]
    B --> PATCH["apply_postgres_schema_patches()"]
    B -.->|"drifted — stamped at a lost revision"| AL["alembic/"]
    PATCH --> PG[("PostgreSQL")]

    classDef warn fill:#f8d7da,stroke:#c00,color:#000
    class AL warn
```

| Action | Do this |
| --- | --- |
| New ORM model | register in `app/all_models.py` **and** the domain's `models/__init__.py` |
| New column | add via `apply_postgres_schema_patches()` — **not** `alembic upgrade` |
| sqlite test | `Base.metadata.create_all` fails (a model uses a Postgres `ARRAY`); create only the table under test |

---

## 6. Conventions worth not relearning

| Area | Rule |
| --- | --- |
| LLM calls | LangChain only (`ChatAnthropic`). Raw `anthropic` imports allowed **only** for exception classes. |
| Temperature | Every `ChatAnthropic(...)` pins `temperature` as a **literal** — a scan test enforces it. |
| Async | `get_db()` yields `AsyncSession`; every DB call is awaited. |
| Naming | routers end `_router.py`; services end `_service.py`. |
| Observability | PostHog only. Never add a second APM agent alongside the OTel SDK. |
| Reference docs | `AI_Agents/Reference_docs/` refresh **manually**, never as a side effect of a code change. |
