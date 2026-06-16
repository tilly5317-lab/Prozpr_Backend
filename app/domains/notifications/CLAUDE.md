# app/domains/notifications/ — in-app notification records + read state

## Layers

- **models/** — `Notification` (user-scoped row: title, message, `notification_type`, `is_read`, optional `action_url`).
- **schemas/** — notification response payloads.
- **routers/** — `/notifications` — list, mark-one-read, mark-all-read.
- **services/** — `notification_service.create_notification` — inserts one row.

## Gotchas & invariants

- **Delivery is pull-based, not push.** `create_notification` only does `db.add` + `flush` synchronously — there is no email / websocket / push channel; the client surfaces a notification by polling the `/notifications` list endpoint (`services/notification_service.py`).
- **Nothing triggers a notification yet:** `create_notification` has zero callers in `app/`. The write path exists but is unwired — adding a producer is a TODO, not a regression (`services/notification_service.py`).

## Don't read

- `__pycache__/`.
