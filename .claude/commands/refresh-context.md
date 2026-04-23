---
description: Reconcile the nearest CLAUDE.md with the current folder structure and report drift.
---

You are reconciling a context layer file (`CLAUDE.md`) against the current state of its folder.

Procedure:

1. Identify the nearest `CLAUDE.md` by walking up from the current working directory. That file is the reconciliation target.

2. Read the target `CLAUDE.md` and detect its template by structural cues, in order:
   - Header contains a "STATUS" line near the top → **Stub**.
   - Presence of `## Child modules` section → **Map**.
   - Presence of `## Files` section → **Leaf**.
   - Fallback → report "unrecognized template, leaving file unchanged" and stop.

3. Scan the target folder and compare against the template-appropriate claims:
   - **Map**: scan subfolders one level deep; compare against the "Child modules" bullet list. Report new, removed, or renamed subfolders.
   - **Leaf**: scan top-level files in the folder; compare against the "Files" bullet list. Report new, removed, or renamed files.
   - **Stub**: re-verify the "Imported by active code?" claim by grepping for imports of this folder from active code paths.

4. If drift is found, update only the stale sections in place. Preserve the "write for stability" discipline:
   - Describe purpose, file roles, contracts, entry points, dependencies.
   - Do NOT introduce specific internal function names, line counts, or algorithms.
   - Interface-level names (classes, modules, exported functions) are fine.
   - Length caps: Map ≤ 80, Leaf ≤ 60, Stub ≤ 20. Flow-bearing files (spec §5.1) may go to Map ≤ 100, Leaf ≤ 80.

5. If no drift is found, say so and change nothing.

6. Report a short diff-style summary of what changed (or "no changes").

After review, commit the updated CLAUDE.md alongside the structural change that prompted the refresh.
