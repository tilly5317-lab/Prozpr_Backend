# app/services/chat_brain/ — Standalone chat brain (mirror)

Standalone mirror of `chat_core.ChatBrain` for single-question runs outside
the HTTP router. Not imported by the live chat endpoint — kept as a developer
experiment. Keep behaviour in sync when touching portfolio or allocation flow.

## Files

- `brain.py` — mirrored `ChatBrain` entry; same intent-branch structure as
  `chat_core/brain.py`.

## Entry point

- `ChatBrain` (from `brain.py`) — not imported by live routes; used by
  developer scripts and experiments.

## Depends on

- `app/services/ai_bridge/` — same bridge functions as `chat_core/`.
- `app/services/ai_module_telemetry` — `log_chat_turn_flow_summary`.
- `app/services/chat_core/types` — `ChatBrainResult`, `ChatTurnInput` DTOs.

## Don't read

- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
