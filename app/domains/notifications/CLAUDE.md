# app/domains/notifications/ — in-app notification records + read state

## Layers

- **models/** — `Notification` (user-scoped row: title, message, `notification_type`, `is_read`, optional `action_url`).
- **schemas/** — notification response payloads.
- **routers/** — `/notifications` — list, mark-one-read, mark-all-read.
- **services/** — `notification_service.create_notification` — inserts one row.

## Gotchas & invariants

- **Delivery is pull-based, not push.** `create_notification` only does `db.add` + `flush` synchronously — there is no email / websocket / push channel; the client surfaces a notification by polling the `/notifications` list endpoint (`services/notification_service.py`).
- **One producer today:** the advisory team-call booking fires a best-effort confirmation notification (`advisory/routers/team_call_router.py`) — a notification hiccup never fails the booking. No other write path is wired.

## Don't read

- `__pycache__/`.
