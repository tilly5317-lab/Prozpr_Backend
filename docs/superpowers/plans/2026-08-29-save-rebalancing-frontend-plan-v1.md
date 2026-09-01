# Save a Rebalancing Plan — Frontend Implementation Plan (v1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the customer a "Save this plan" pill on a rebalancing recommendation in chat, and make the portfolio page show the saved plan (else the latest) — the frontend half of the [backend v1 plan](2026-08-27-save-rebalancing-plan-v1.md).

**Architecture:** Three surgical changes in the **separate** `Prozpr_Frontend` repo. (1) Two one-line API wrappers in the existing `src/lib/api.ts` fetch layer. (2) The chat panel captures the rebalancing run id the backend now returns and renders a Save pill that POSTs it. (3) The rebalancing page reads a new saved-else-latest endpoint and shows a "Saved plan" badge. No new libraries, no state-management change, no routing change.

**Tech Stack:** React 18 + Vite 5 (SPA) · TypeScript · Tailwind + shadcn/ui · React Context (no Redux/Zustand) · **manual `fetch` via `src/lib/api.ts` — React Query is installed but unused; do NOT introduce it** · sonner + shadcn toasts · vitest + @testing-library/react (jsdom).

---

## Global Constraints

- **Repo:** all paths below are in `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend` (a different git repo from the backend). Commit there, separately from the backend.
- **Backend dependency — ship the backend first.** This plan consumes three backend contracts from the [v1 backend plan](2026-08-27-save-rebalancing-plan-v1.md): `ideal_allocation_rebalancing_id` on the chat send-message response (backend Task 5), `origin` on the rebalancing run list/detail responses (backend Task 1), and the `POST /rebalancing/{run_id}/save` + `GET /rebalancing/current` endpoints (backend Task 4). Against a backend without these, the pill never appears (id is null), `/rebalancing/current` 404s as "not found," and the badge never shows. Verify the backend is deployed to the target environment before manual verification.
- **Match the existing pattern, not the design system.** In-chat CTAs and the page's Recalculate control are hand-rolled `<button>`s with inline Tailwind (`AIChatPanel.tsx:1888,1914`, `RebalanceExplanation.tsx:773`) — the new pill/badge match those siblings, NOT the shared `<Button>`/`<Badge>` components.
- **Data fetching:** manual `request<T>()` wrappers in `src/lib/api.ts`; components call them with `useState` + `async/await` (+ `.catch(() => fallback)`), mirroring `RebalanceGate.tsx:208-233`. No `useQuery`/`useMutation`.
- **Toasts:** the chat panel imports no toast today; use sonner (`import { toast } from "sonner"`) — `<Sonner/>` is already mounted globally in `App.tsx`.
- **Path alias:** `@/` → `src/`.

## Testing strategy (read before Task 1)

The harness is fully wired (`vitest.config.ts`: jsdom, globals, `src/test/setup.ts` with jest-dom; `@testing-library/react` installed) and a handful of tests already exist — lib tests (`src/lib/buildUp.test.ts`, `twr.test.ts`) and two component tests (`src/components/dashboard/CurrentAllocationCard.test.tsx`, `src/pages/GoalsTimeline.test.tsx`). Both unit and component testing have precedent here.

- **Task 1 (API layer) is unit-tested** with a mocked `fetch` — cheap, stable, high value.
- **Tasks 2–3 (UI) are verified manually** against `npm run dev`. Rationale is ROI, not "no culture": both touch points live inside the 108 KB `AIChatPanel` and the large `RebalanceExplanation`, which drag in streaming, several React contexts, framer-motion, and routing — rendering either under RTL for a ~15-line pill and a two-line fetch swap is high-effort and low-assurance, unlike the small standalone components that carry tests today. (Optional upgrade for automated pill coverage: extract a presentational `<SavePlanPill runId saved saving onSave/>` and unit-test that in isolation — the existing component tests are the pattern to copy. Flagged, not required.)

**Typecheck gate:** use `npx tsc -p tsconfig.app.json --noEmit`. The root `tsconfig.json` is solution-style (`"files": []` + project references), so a plain `tsc --noEmit` type-checks **zero** files and always exits 0 — it is not a real gate.

Run tests: `npm run test` (one-shot) or `npm run test:watch`.

---

### Task 1: API layer — `saveRebalancingRun` + `getCurrentRebalancingRun` + `origin` field

**Files:**
- Modify: `src/lib/api.ts` (add `origin` to `RebalancingRunListItem` ~line 2696-2704; add two exports after `getRebalancingRunDetail` at line 2742)
- Test: `src/lib/rebalancing-save.test.ts` (new — the repo's first real test file)

**Interfaces:**
- Consumes: the existing private `request<T>(path, init?)` wrapper (`api.ts:168`), the existing `RebalancingRunListItem` and `RebalancingRunDetail` types.
- Produces:
  - `saveRebalancingRun(runId: string): Promise<RebalancingRunListItem>` → `POST /rebalancing/${runId}/save`
  - `getCurrentRebalancingRun(): Promise<RebalancingRunDetail>` → `GET /rebalancing/current` (rejects with a 404-bearing `Error` when the user has no runs)
  - `RebalancingRunListItem.origin?: string \| null` (inherited by `RebalancingRunDetail`)

- [ ] **Step 1: Write the failing test**

Create `src/lib/rebalancing-save.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// request<T> reads localStorage token and parses via res.text()+JSON.parse (never res.json()).
// None of these mocks hit a 502/503/504 or a fetch reject, so the module's
// `backendOfflineUntil` guard stays at 0 and tests do not leak offline state into each other.
function mockFetchOnce(body: unknown, opts: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = opts;
  return vi.fn().mockResolvedValue({
    ok,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response);
}

describe("rebalancing save-plan api", () => {
  beforeEach(() => {
    localStorage.setItem("askProzpr_token", "test-token");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("saveRebalancingRun POSTs to /rebalancing/{id}/save", async () => {
    const fetchMock = mockFetchOnce({ id: "run-1", origin: "saved" });
    vi.stubGlobal("fetch", fetchMock);
    const { saveRebalancingRun } = await import("@/lib/api");

    const res = await saveRebalancingRun("run-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/rebalancing\/run-1\/save$/);
    expect((init as RequestInit).method).toBe("POST");
    expect(res.origin).toBe("saved");
  });

  it("getCurrentRebalancingRun GETs /rebalancing/current", async () => {
    const fetchMock = mockFetchOnce({ id: "run-9", origin: null, trades: [] });
    vi.stubGlobal("fetch", fetchMock);
    const { getCurrentRebalancingRun } = await import("@/lib/api");

    const res = await getCurrentRebalancingRun();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/rebalancing\/current$/);
    expect(((init as RequestInit)?.method ?? "GET")).toBe("GET");
    expect(res.id).toBe("run-9");
  });

  it("getCurrentRebalancingRun rejects on 404 (no runs yet)", async () => {
    vi.stubGlobal("fetch", mockFetchOnce({ detail: "No rebalancing runs" }, { ok: false, status: 404 }));
    const { getCurrentRebalancingRun } = await import("@/lib/api");

    await expect(getCurrentRebalancingRun()).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- rebalancing-save`
Expected: FAIL — `saveRebalancingRun` / `getCurrentRebalancingRun` are not exported from `@/lib/api`.

- [ ] **Step 3: Add `origin` to `RebalancingRunListItem`**

In `src/lib/api.ts`, in `RebalancingRunListItem` (the interface at ~line 2696), add the field after `updated_at`:

```ts
export interface RebalancingRunListItem {
  id: string;
  portfolio_id: string;
  source_allocation_run_id: string;
  status: RebalancingStatus;
  engine_version: string;
  created_at: string;
  updated_at: string;
  /** "saved" once the customer commits this run via POST /rebalancing/{id}/save; null otherwise. */
  origin?: string | null;
}
```

(`RebalancingRunDetail extends RebalancingRunListItem`, so it inherits `origin` automatically.)

- [ ] **Step 4: Add the two API functions**

In `src/lib/api.ts`, immediately after `getRebalancingRunDetail` (ends at line 2742):

```ts
/** Mark a rebalancing run as the customer's committed plan. Idempotent: saving
 *  the same run twice is a no-op on the backend. Returns the updated run. */
export async function saveRebalancingRun(runId: string): Promise<RebalancingRunListItem> {
  return request<RebalancingRunListItem>(`/rebalancing/${runId}/save`, { method: "POST" });
}

/** The customer's committed run (origin="saved") if any, else the latest run.
 *  Rejects with a 404-bearing Error when the customer has no runs at all —
 *  callers treat that as "no plan yet" (see RebalanceExplanation.loadData). */
export async function getCurrentRebalancingRun(): Promise<RebalancingRunDetail> {
  return request<RebalancingRunDetail>("/rebalancing/current");
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npm run test -- rebalancing-save`
Expected: PASS (3 tests).

- [ ] **Step 6: Typecheck**

Run: `npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add src/lib/api.ts src/lib/rebalancing-save.test.ts
git commit -m "feat(rebalancing): add saveRebalancingRun + getCurrentRebalancingRun api"
```

---

### Task 2: Chat — capture the run id and render the "Save this plan" pill

**Files:**
- Modify: `src/components/chat/AIChatPanel.tsx` (Message type ~line 64-85; imports; component hooks; response consumer ~line 1587-1598; pill render after ~line 1928)

**Interfaces:**
- Consumes: `resp.ideal_allocation_rebalancing_id` (backend Task 5 — already declared on `ChatSendResponse` at `api.ts:944`), `saveRebalancingRun` (Task 1).
- Produces: user-visible pill only; no exported symbols.

**Context — the field already exists, it just wasn't sent.** `ChatSendResponse.ideal_allocation_rebalancing_id` is already declared (`api.ts:944`) and already read at `AIChatPanel.tsx:1589` inside `hasSavedPlan`. Backend Task 5 makes the server actually populate it. Two consequences to design around:
  1. On a plain rebalancing turn the id is now present, so the **existing "View recommended plan" pill** (`showViewExecutePlan`, `AIChatPanel.tsx:1914`) starts appearing there (it is dark today). That is intended — leave the `hasSavedPlan` line untouched.
  2. The **Save pill keys on the rebalancing id specifically** (`msg.rebalancingRunId`), NOT on the `?? ideal_allocation_snapshot_id` fallback — you cannot "save" an asset-allocation snapshot as a rebalancing plan. Both pills can appear together on a rebalancing turn; only "View" appears on a snapshot-only asset-allocation turn.

- [ ] **Step 1: Add `rebalancingRunId` to the `Message` type**

In `src/components/chat/AIChatPanel.tsx`, in the `Message` interface (line 64), after `showViewExecutePlan?: boolean;` (line 74):

```ts
  /** The persisted rebalancing run this AI turn produced (backend
   *  `ideal_allocation_rebalancing_id`). Enables the "Save this plan" pill,
   *  which POSTs it to /rebalancing/{id}/save. Absent on tilt / redirect turns
   *  and on asset-allocation-only turns (backend sends no rebalancing id). */
  rebalancingRunId?: string;
```

- [ ] **Step 2: Add imports**

At the top of the file, add sonner and the api function, and one lucide icon.
- Add a new import line: `import { toast } from "sonner";`
- In the existing `@/lib/api` import, add `saveRebalancingRun`.
- In the existing `lucide-react` import (line 4 — the one that includes `ArrowRight`, `UploadCloud`, and already `Check`), add **only `Bookmark`**. **Do NOT add `Check`** — it is already imported on that line; a second `Check` is a duplicate identifier that fails the Vite/esbuild transform, breaking both `npm run dev` and `npm run build`.

- [ ] **Step 3: Add save state + handler among the component hooks**

Near the top of the `AIChatPanel` component body — search for `const [isTyping` to find the hook cluster — add:

```ts
  const [savingRunId, setSavingRunId] = useState<string | null>(null);
  const [savedRunIds, setSavedRunIds] = useState<Set<string>>(new Set());

  const handleSavePlan = useCallback(async (runId: string) => {
    setSavingRunId(runId);
    try {
      await saveRebalancingRun(runId);
      setSavedRunIds((prev) => new Set(prev).add(runId));
      toast.success("Plan saved to your portfolio");
    } catch {
      toast.error("Couldn't save the plan. Please try again.");
    } finally {
      setSavingRunId(null);
    }
  }, []);
```

(`useState`/`useCallback` are already imported — the component uses them throughout.)

- [ ] **Step 4: Capture the id when the streamed turn completes**

In the SSE completion handler, the `finalMessage` is built at `AIChatPanel.tsx:1592-1598`. **Leave the `hasSavedPlan` line (1588-1590) exactly as is.** Add a `rebalancingRunId` capture and spread it into `finalMessage`:

```ts
      const hasSavedPlan = Boolean(
        resp.ideal_allocation_rebalancing_id ?? resp.ideal_allocation_snapshot_id
      );
      const rebalancingRunId = resp.ideal_allocation_rebalancing_id ?? undefined;
      // done is authoritative — replace the streamed text, never append to it.
      const finalMessage: Message = {
        role: "ai",
        content: resp.assistant_message.content,
        ...(hasSavedPlan ? { showViewExecutePlan: true } : {}),
        ...(rebalancingRunId ? { rebalancingRunId } : {}),
        ...(resp.portfolio_data_missing ? { showAddCams: true } : {}),
        chartPayloads: resp.assistant_message.chart_payloads || null,
      };
```

- [ ] **Step 5: Render the Save pill**

In the assistant-message branch, immediately after the `showViewExecutePlan` pill's closing `) : null}` (line 1928), add a third sibling pill:

```tsx
              {msg.rebalancingRunId ? (
                <button
                  type="button"
                  disabled={
                    savedRunIds.has(msg.rebalancingRunId) ||
                    savingRunId === msg.rebalancingRunId
                  }
                  onClick={() => void handleSavePlan(msg.rebalancingRunId)}
                  className="ml-7 mt-2 self-start flex items-center gap-3 rounded-xl px-4 py-3 transition-opacity hover:opacity-90 border border-primary/25 bg-primary/5 disabled:cursor-default disabled:opacity-60 disabled:hover:opacity-60"
                >
                  <div className="flex flex-col text-left">
                    <span className="text-[11px] font-medium text-muted-foreground">
                      {savedRunIds.has(msg.rebalancingRunId)
                        ? "Saved to your portfolio"
                        : "Make this your plan"}
                    </span>
                    <span className="text-[13px] font-semibold text-foreground">
                      {savedRunIds.has(msg.rebalancingRunId)
                        ? "Plan saved"
                        : savingRunId === msg.rebalancingRunId
                          ? "Saving…"
                          : "Save this plan"}
                    </span>
                  </div>
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15">
                    {savedRunIds.has(msg.rebalancingRunId) ? (
                      <Check className="h-4 w-4 text-primary" />
                    ) : (
                      <Bookmark className="h-4 w-4 text-primary" />
                    )}
                  </div>
                </button>
              ) : null}
```

(No non-null assertion is needed: this repo sets `strictNullChecks: false`, and at runtime the pill only renders under the `{msg.rebalancingRunId ? … : null}` guard, so `handleSavePlan` and `savedRunIds.has(...)` always receive a real string.)

- [ ] **Step 6: Typecheck**

Run: `npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors.

- [ ] **Step 7: Manual verification** (dev server; backend Task 5 + Task 2/4 deployed)

Run `npm run dev`, open the chat, ask for a rebalancing (e.g. "rebalance my portfolio"). Verify:
- A "Save this plan" pill appears under the reply (alongside "View recommended plan").
- Click it → text goes "Saving…" → toast "Plan saved to your portfolio" → pill shows "Plan saved" with a check and is disabled.
- Ask a non-rebalancing question (e.g. "what's the market outlook?") → **no** Save pill.
- (After Task 3) the saved run is what `/invest/rebalance-explanation` shows.

- [ ] **Step 8: Commit**

```bash
git add src/components/chat/AIChatPanel.tsx
git commit -m "feat(chat): add Save this plan pill for rebalancing recommendations"
```

---

### Task 3: Portfolio page — read saved-else-latest + "Saved plan" badge

**Files:**
- Modify: `src/pages/RebalanceExplanation.tsx` (import ~line 12-25; `loadData` ~line 528-547; header row ~line 771-781)

**Interfaces:**
- Consumes: `getCurrentRebalancingRun` (Task 1), `detail.origin` (Task 1 type field, backend Task 1 data).
- Produces: none.

**Do NOT touch `compute()`** (line 501-522). It runs a fresh rebalancing via the Recalculate button and must display the run it just computed — `listRebalancingRuns()[0]` (latest) is correct there. Switching it to `getCurrentRebalancingRun()` would wrongly re-show an older *saved* run right after the user recalculated. Only `loadData()` (initial page load) becomes saved-else-latest.

- [ ] **Step 1: Add the import**

In `src/pages/RebalanceExplanation.tsx`, in the `@/lib/api` import block (lines 12-25), add `getCurrentRebalancingRun` (keep `listRebalancingRuns` — `compute()` still uses it):

```ts
import {
  getCurrentRebalancingRun,
  getMyPortfolio,
  getRebalanceComputeProgress,
  getRebalancingRunDetail,
  listRebalancingRuns,
  runRebalancing,
  // …rest unchanged…
} from "@/lib/api";
```

- [ ] **Step 2: Switch `loadData` to saved-else-latest**

Replace the body of `loadData` (lines 528-547) — the two-step `listRebalancingRuns()[0]` + `getRebalancingRunDetail(run.id)` collapses into the single `/current` call, which returns the full detail directly:

```ts
  const loadData = useCallback(async () => {
    setDataLoading(true);
    setDataError(null);
    try {
      // Saved run (origin="saved") if the customer committed one, else the latest.
      // 404 (no runs at all) → null → compute one, exactly as the old empty branch did.
      const current = await getCurrentRebalancingRun().catch(() => null);
      if (!current) {
        setDataLoading(false);
        await compute();
        return;
      }
      setDetail(current);
      // Best-effort: load holdings so we can show the funds we're keeping.
      getMyPortfolio().then(setPortfolio).catch(() => { /* section just hides */ });
    } catch {
      setDataError("Couldn't load your rebalancing plan. Please try again.");
    } finally {
      setDataLoading(false);
    }
  }, [compute]);
```

- [ ] **Step 3: Add the "Saved plan" badge**

In the header row (lines 771-781), between the "Rebalancing" label and the Recalculate button (the button keeps `ml-auto`, so it stays right-aligned), add the badge:

```tsx
            <div className="-mb-1 flex items-center gap-2">
              <span className="text-lg font-semibold text-foreground">Rebalancing</span>
              {detail.origin === "saved" ? (
                <span className="flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-[11px] font-semibold text-primary">
                  <Bookmark className="h-3 w-3" />
                  Saved plan
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => void compute()}
                className="ml-auto flex items-center gap-1 rounded-full border border-border px-3 py-1.5 text-[11.5px] font-semibold text-foreground transition-colors hover:bg-muted/50"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Recalculate
              </button>
            </div>
```

(`detail` is non-null in this block — it is guarded by `… && detail && (` at line 769.)

- [ ] **Step 4: Add the `Bookmark` icon import**

In the `lucide-react` import (line 4: `ArrowRight, Loader2, Lock, RefreshCw, Sparkles`), add `Bookmark`:

```ts
import { ArrowRight, Bookmark, Loader2, Lock, RefreshCw, Sparkles } from "lucide-react";
```

- [ ] **Step 5: Typecheck**

Run: `npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors.

- [ ] **Step 6: Manual verification** (dev server; backend deployed)

- With a **saved** run: open `/invest/rebalance-explanation` → the "Saved plan" badge shows; the page reflects the saved run's trades even if a newer unsaved run exists.
- With **no saved** run but some runs: badge absent; page shows the latest run (unchanged behavior).
- With **no runs at all**: page computes one exactly as before (the 404 → `null` → `compute()` path).
- Recalculate → shows the just-computed run (not the old saved one); badge disappears (fresh run has `origin=null`).

- [ ] **Step 7: Commit**

```bash
git add src/pages/RebalanceExplanation.tsx
git commit -m "feat(invest): show saved-else-latest rebalancing plan + Saved badge"
```

---

## Self-review checklist (run after implementing, before opening a PR)

- [ ] **Backend deployed first** — the target environment serves `ideal_allocation_rebalancing_id`, `origin`, `POST /save`, `GET /current`. Otherwise the pill/badge silently no-op.
- [ ] **`compute()` untouched** — only `loadData()` switched to `/current`.
- [ ] **`hasSavedPlan` line untouched** — the Save pill keys on `msg.rebalancingRunId`, the View pill on the existing `hasSavedPlan`.
- [ ] **No React Query** introduced; toasts use sonner; pills are hand-rolled `<button>`s.
- [ ] `npx tsc -p tsconfig.app.json --noEmit` clean; `npm run test` green.

## Out of scope / known limitations (v1)

- **The Save pill shows on live turns only, not on rehydrated chat history.** The session-restore paths (`AIChatPanel.tsx:1172,1301`) rebuild messages from persisted history, which carries no per-message run id — so reopening an old session shows no Save pill on past rebalancing turns (identical to today's View-pill behavior). Re-asking produces a fresh pill. Acceptable for v1.
- Saving a **tilted** plan, persisting a tilt preference, and the "Saved plan" surviving a CAMS re-upload are **v2** (see the [design spec](../specs/2026-08-27-save-rebalancing-plan-design.md)). In v1 a CAMS re-upload wipes the saved run and the page returns to latest/empty — the customer re-saves. No execution/SIP UI changes.
