# AI Modules — intent → flow → domain → agent

Every chat turn resolves to **one intent**. The intent picks **one flow**. The flow calls **domains** in order. Each domain calls **one agent** under `AI_Agents/src`.

**Choose your intent in §3** — each opens to its own diagram.

---

## 1. The chain, once

```mermaid
flowchart LR
    Q["customer question"] --> IC["intent_classifier<br/><i>Haiku, structured output</i>"]
    IC --> IN{"intent"}
    IN --> FL["FLOWS[intent]<br/>ai_engine/services/flow.py"]
    FL --> DS["&lt;domain&gt;/services/<br/>&lt;domain&gt;_module_service.run"]
    DS --> AG["AI_Agents/src/&lt;agent&gt;"]
    AG --> FP["facts pack"]
    FP --> AF["answer_formatter<br/><i>writes EVERY reply</i>"]
    AF --> OUT["ModuleOutput.text"]

    classDef hot fill:#d1ecf1,stroke:#0c5460,color:#000
    class AF hot
```

> **Adding an intent = one `flow_*` function + one `FLOWS` row.** The brain never changes.

---

## 2. The four invariants

```mermaid
flowchart TB
    R1["1 · A domain never calls another domain<br/><i>cross-domain data rides the flow's prior dict</i>"]
    R2["2 · Every reply is written by answer_formatter<br/><i>a domain supplies FACTS + a body prompt, never prose</i>"]
    R3["3 · One gateway per agent<br/><i>only the owning *_module_service imports AI_Agents.&lt;x&gt;</i>"]
    R4["4 · Delegate AI to AI_Agents/src<br/><i>never hand-roll ChatAnthropic for a reply an agent already produces</i>"]

    classDef rule fill:#fff3cd,stroke:#d39e00,color:#000
    class R1,R2,R3,R4 rule
```

**Why #2 exists:** domains once each owned their reply LLM call — the house rules got copy-pasted into three prompts and token-streaming had to land in four places.

**Corollary:** the formatter's tool may gain non-prose fields (booleans, enums, short control strings) but **never a second prose field** — one competes with `answer` and returns nothing about half the time on long replies.

---

## 3. Pick an intent

<details>
<summary><b>▸ asset_allocation</b> — "what mix should I be in?"</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_asset_allocation
    participant D as asset_allocation domain
    participant A as AI_Agents/src/asset_allocation_pydantic
    participant AF as answer_formatter
    participant DB as PostgreSQL

    B->>F: intent = asset_allocation
    F->>D: run(turn, ctx, {})
    D->>D: input_builder → AllocationInput
    D->>A: run_allocation_with_state
    Note over A: pure Python — the one LLM<br/>touch (step-7 rationale) is opt-OUT
    A-->>D: GoalAllocationOutput
    D->>DB: aa_engine/persistence — allocation run + targets + buckets
    D->>AF: facts pack
    AF-->>D: reply text
    D-->>B: ModuleOutput(text, payload, persisted_run_id)
```

`sequence = [asset_allocation]` · agent is **pure Python**, not an LLM pipeline.

</details>

<details>
<summary><b>▸ rebalancing</b> — "what should I buy and sell?"</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_rebalancing
    participant P as practical_asset_allocation domain
    participant R as rebalancing domain
    participant AP as AI_Agents/src/practical_asset_allocation
    participant AR as AI_Agents/src/Rebalancing
    participant DB as PostgreSQL

    B->>F: intent = rebalancing
    F->>P: run(turn, ctx, {})
    P->>AP: holdings-aware allocation
    AP-->>P: targets
    P->>DB: PERSIST a fresh allocation run
    F->>R: run(turn, ctx, prior={asset_allocation: paa})
    R->>DB: cache-first lookup (90-day TTL) reads that run as its target
    R->>AR: ideal allocation + holdings → per-fund buy/sell
    Note over AR: tax-aware sell prioritisation
    AR-->>R: trades + warnings
    R->>DB: rebalancing run, trades, subgroup summaries
    R-->>B: ModuleOutput (rebalancing owns the reply)
```

> **Coupling is via the DB, not `prior`.** The `prior` dict here is informational — `rebalancing_module_service` does not read it. Dropping the PAA step would not save compute; it would silently rebalance against an allocation up to 90 days stale.

</details>

<details>
<summary><b>▸ goal_planning</b> — "can I afford this goal?"</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_goal_planning
    participant C as cashflow domain
    participant A as AI_Agents/src/cashflow_statement
    participant AF as answer_formatter

    B->>F: intent = goal_planning
    F->>C: run(turn, ctx, {})
    C->>C: readiness gate — real inputs, no placeholders
    alt required inputs missing
        C-->>B: apology / ask for the missing field
    else ready
        C->>A: NL goal extraction + lever proposal (LangChain)
        C->>A: pure-Python projection pipeline
        A-->>C: CashflowProjection
        C->>AF: facts pack (+ chart payloads)
        AF-->>C: reply text
        C-->>B: ModuleOutput
    end
```

Uses `financial_primitives/` — the shared numeric kernel (pure functions, no LLM, no I/O).

</details>

<details>
<summary><b>▸ additional_investment</b> — "I have fresh money to deploy"</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_additional_investment
    participant D as additional_investment domain
    participant A as AI_Agents/src/additional_investment

    B->>F: intent = additional_investment
    F->>D: run(turn, ctx, {})
    D->>D: extract amount + cadence (lumpsum vs SIP)
    D->>D: SELF-PRIME practical asset allocation
    Note right of D: needs the persisted allocation RUN<br/>for source_allocation_run_id —<br/>so the flow must NOT pre-run PAA
    D->>A: AdditionalInvestmentInput
    Note over A: BUY-only. lumpsum fills deficits,<br/>SIP follows the ideal mix
    A-->>D: per-fund deployment plan
    D-->>B: ModuleOutput
```

The one flow that deliberately does **not** pre-run PAA — doing so would compute the allocation twice.

</details>

<details>
<summary><b>▸ portfolio_query</b> — "how is my portfolio doing?" (read-only)</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_portfolio_query
    participant P as portfolio domain
    participant A as AI_Agents/src/portfolio_query
    participant HV as house_view.py
    participant AF as answer_formatter

    B->>F: intent = portfolio_query
    F->>P: answer_portfolio_query(question, ctx)
    P->>A: build facts pack (client profile + holdings)
    opt "fund_house_view" in ctx.tools_needed
        A->>HV: load_house_view(prozpr_only=True)
        Note over HV: allow-list — emits only<br/>"Prozpr view:" paragraphs,<br/>so no fund house can leak
    end
    A-->>P: facts + skill prompt + scope guardrails
    P->>AF: facts pack
    AF-->>P: reply text
    P-->>B: ModuleOutput(text) — NO persistence
```

</details>

<details>
<summary><b>▸ mutual_fund_query</b> — "is this a good fund?" (read-only)</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_mutual_fund_query
    participant M as mutual_funds domain
    participant A as AI_Agents/src/mutual_fund_query
    participant AF as answer_formatter

    B->>F: intent = mutual_fund_query
    F->>M: answer_mutual_fund_query(question, ctx)
    M->>A: forced-tool Haiku extract (which fund?)
    A-->>M: resolved scheme
    M->>M: fund-ranking CSV + stored NAV
    M->>AF: grounded facts pack
    AF-->>M: reply text
    M-->>B: ModuleOutput(text) — NO persistence
```

About **funds themselves**, held or not — the agent is DB-agnostic; the domain supplies the data.

</details>

<details>
<summary><b>▸ general_market_query</b> — "what are the markets doing?"</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_market
    participant MC as market_commentary domain
    participant HV as fund_house_view service
    participant GC as general_chat domain

    B->>F: intent = general_market_query<br/>ctx.tools_needed from classifier
    alt "market_commentary" in tools (or neither named)
        F->>MC: run → live factual macro data
        MC-->>F: capped at 15k chars
    end
    opt "fund_house_view" in tools
        F->>HV: run → Prozpr stance + fund-house outlooks
        HV-->>F: capped at 90k chars
    end
    F->>GC: run(turn, ctx, prior={market_commentary: combined})
    Note over GC: general_chat writes the final reply
    GC-->>B: ModuleOutput
```

> `tools_needed` is a **fetch list, not routing**. Empty means the customer's own record suffices. Loading commentary unconditionally used to make the model compare an allocation % against a P/E.

</details>

<details>
<summary><b>▸ general_chat</b> — fallback when no specialist owns the intent</summary>

```mermaid
sequenceDiagram
    autonumber
    participant B as ChatBrain
    participant F as flow_general_chat
    participant G as general_chat domain
    participant AN as Anthropic (web search)

    B->>F: unknown intent → default
    F->>G: run(turn, ctx, {})
    G->>AN: research + compose
    AN-->>G: answer
    G-->>B: ModuleOutput
```

Also the landing spot for any intent name not present in `FLOWS`.

</details>

<details>
<summary><b>▸ out_of_scope / stock_advice</b> — classifier-only, short-circuits</summary>

```mermaid
flowchart LR
    IC["intent_classifier"] --> OS{"out_of_scope<br/>or stock_advice?"}
    OS -->|no| FLOW["FLOWS[intent]"]
    OS -->|yes| CAN{"raw.out_of_scope_message set?"}
    CAN -->|yes| RD["general_chat.format_redirect_or_canned<br/><i>tailored redirect</i>"]
    CAN -->|no| PLAIN["canned message"]
    RD --> FIN["_finalize"]
    PLAIN --> FIN
```

Subreasons: `gibberish · identity_or_meta · security_or_credentials · chat_summary · off_topic · other`.
**No flow runs, no domain is touched.**

</details>

---

## 4. Two gates before any flow runs

```mermaid
flowchart TD
    IC["intent classified"] --> G1{"classifier-only intent?"}
    G1 -->|yes| SHORT["canned / tailored redirect"]
    G1 -->|no| G2{"is_portfolio_data_missing?"}
    G2 -->|yes| CAMS["honest reply + add-CAMS CTA flag<br/><i>fails OPEN</i>"]
    G2 -->|no| FLOW["run FLOWS[intent] under timeout"]
    FLOW -->|"asyncio.TimeoutError"| FB["rollback + fallback message"]
    FLOW -->|ok| FIN["_finalize"]

    classDef gate fill:#fff3cd,stroke:#d39e00,color:#000
    class G1,G2 gate
```

CAMS is skippable at onboarding, so a customer can reach chat with nothing imported. Running a holdings-driven engine over an empty portfolio yields either a technical blocking message or example numbers the customer reads as their own.

---

## 5. Intent → domain → agent

| Intent | Flow | Domain(s), in order | `AI_Agents/src` | Writes? |
| --- | --- | --- | --- | --- |
| `asset_allocation` | `flow_asset_allocation` | asset_allocation | `asset_allocation_pydantic` | ✅ |
| `rebalancing` | `flow_rebalancing` | practical_asset_allocation → rebalancing | `practical_asset_allocation`, `Rebalancing` | ✅ |
| `goal_planning` | `flow_goal_planning` | cashflow | `cashflow_statement`, `financial_primitives` | ✅ |
| `additional_investment` | `flow_additional_investment` | additional_investment | `additional_investment` | ✅ |
| `portfolio_query` | `flow_portfolio_query` | portfolio | `portfolio_query`, `house_view` | ❌ |
| `mutual_fund_query` | `flow_mutual_fund_query` | mutual_funds | `mutual_fund_query` | ❌ |
| `general_market_query` | `flow_market` | market_commentary (+fund_house_view) → general_chat | `market_commentary`, `house_view` | ❌ |
| `general_chat` | `flow_general_chat` | general_chat | — (Anthropic web search) | ❌ |
| `out_of_scope`, `stock_advice` | — | — | — | ❌ |

---

## 6. The shared kernel (`ai_engine/`, package root)

```mermaid
flowchart LR
    subgraph K["ai_engine/ — orchestration only, no domain logic"]
      T["types.py<br/>ModuleOutput · IntentDecision · AIModule"]
      CT["chat_types.py<br/>ChatTurnInput · ChatBrainResult"]
      TC["turn_context.py<br/>history · last runs · active intent"]
      CD["chat_dispatcher.py<br/>per-intent handler registry"]
      CL["classifier_llm.py<br/>Haiku structured output"]
      CM["common.py<br/>ensure_ai_agents_path() · money fmt"]
      ST["streaming.py<br/>re-exports token_stream"]
      TH["thinking.py<br/>live 'thinking aloud' feed"]
      LD["logic_docs.py<br/>module → thesis doc"]
      UT["usage_tracking.py + posthog_tracing.py"]
      AFM["answer_formatter/<br/><b>THE answer stage</b>"]
    end

    classDef hot fill:#d1ecf1,stroke:#0c5460,color:#000
    class AFM hot
```

| File | Why it matters |
| --- | --- |
| `answer_formatter/` | every customer-facing reply is written here — add a facts pack, never a reply call |
| `common.ensure_ai_agents_path()` | the `sys.path` injection that makes `from Rebalancing… import` work |
| `streaming.py` | canonical def lives in `AI_Agents/src` because agents cannot import `app/` |
| `thinking.py` | polled by `GET /chat/sessions/{id}/thinking`; vanishes on reply |
| `usage_tracking.py` | wraps the **whole** turn — one PostHog `$ai_trace`, a generation per LLM call |
| `routers/` | **debug endpoints, not the live chat path** — never wire production behaviour here |

---

## 7. Adding a new intent

```mermaid
flowchart LR
    S1["1 · add to Intent enum<br/>AI_Agents/src/intent_classifier/models.py"]
    S2["2 · write &lt;domain&gt;/services/<br/>&lt;domain&gt;_module_service.run"]
    S3["3 · add flow_&lt;x&gt; + one FLOWS row<br/>ai_engine/services/flow.py"]
    S4["4 · build a facts pack<br/>→ answer_formatter"]
    S1 --> S2 --> S3 --> S4
    S4 -.->|"brain.py unchanged"| DONE["done"]
```

**Do not:** hand-roll a `ChatAnthropic` reply call · import another domain · add a second prose field to the formatter tool · omit a literal `temperature=`.
