---
description: Reconcile the nearest CLAUDE.md with its folder and report drift. Encodes the context-layer convention (v2).
---

You are reconciling a context-layer file (`CLAUDE.md`) against its folder, following the **context-layer convention v2** below.

## Convention

**Type** (detect by structural cue, in order — these markers are load-bearing; keep them):
- `## Imported by active code?` → **Stub**. Stubs are for LEGACY / not-imported folders only; never down-type an active, imported folder to a Stub.
- `## Child modules` or `## Layers` → **Map**.
- `## Files` → **Leaf**.
- none → report "unrecognized template, leaving file unchanged" and stop.

**Template** (sections in order; include one only when it earns its place):
- `# <path>/ — <one-line purpose>` — purpose lives in the header; do NOT repeat it as a lowercased body line.
- `## Entry / contract` *(optional)* — who calls this folder and how; the public entry point / gateway.
- `## Child modules` | `## Layers` | `## Files` — the typed structure. **One idea per bullet.** A substantial engine sub-package gets its OWN Leaf file, not a parent mega-bullet. Never restate a folder name with no added insight.
- `## Gotchas & invariants` *(0..N)* — folder-LOCAL non-obvious contracts, footguns, env flags, invariants. Each carries the *why* and a `file:line` (or symbol) **anchor**. Prefer design/regulatory invariants over implementation trivia. (System-wide landmines belong in `AI_Agents/Reference_docs/ARCHITECTURE.md`, not here.)
- `## Testing` *(optional)* — folder-specific runners only (`dev_run`, `Master_testing/`); never repeat the root.
- `## Don't read` — caches / generated artifacts.

**Write for stability — keep vs drop:**
- KEEP: entry/gateway contracts; cross-module sentinels & invariants; stable numeric conventions (day-count, sign, rounding, regulatory rules); runtime-loaded contract files + what breaks if changed; env flags; reverse-dependency notes.
- DROP: test-file rosters; imported-symbol lists (keep the *edge*, drop the roster); "exactly N files" counts; internal helper names & algorithm sketches; the lowercased title restatement.

**Size — word budgets** (targets + lint-flag, not hard fails):
- Stub ≤ 120 · Leaf ≤ 250 (flow-bearing ≤ 400) · Map ≤ 400 · **Hub ≤ 600**.
- Hub = role-based: orchestration spines, package indexes, multi-stage pipeline maps — `CLAUDE.md` (root), `app/`, `AI_Agents/src/`, `app/domains/ai_engine/`, `AI_Agents/src/cashflow_statement/`.
- Flag any bullet > 30 words, or file > ~11 words/line, for restructuring. Lines are a secondary readability signal, not the cap.

## Procedure

1. Identify the nearest `CLAUDE.md` by walking up from the cwd — that is the target.
2. Read it; detect its type by the cues above.
3. Scan the folder and compare against the type's claims:
   - **Map** — subfolders one level deep vs `## Child modules`/`## Layers`. Report new / removed / renamed.
   - **Leaf** — top-level files + direct subfolders vs `## Files`. Report new / removed / renamed.
   - **Stub** — re-verify `## Imported by active code?` by grepping active code paths (`app/`, `AI_Agents/src/`).
4. **Gotcha-anchor spot-check** — for every `## Gotchas & invariants` bullet citing a `file:line`/symbol, verify the path/symbol still exists; flag stale anchors (the gotcha itself may now be wrong).
5. **Budget & density check** — word-count the file against its budget; flag over-budget files and any > 30-word mega-bullets.
6. If drift is found, update only the stale sections in place, honoring keep/drop and one-idea-per-bullet. If none, say so and change nothing.
7. Report a short diff-style summary (or "no changes"). Leave changes in the working tree for review.
