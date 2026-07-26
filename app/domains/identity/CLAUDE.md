# app/domains/identity/ — user, auth, OTP, family members, linked accounts, onboarding

## Layers

- **models/** — `User`, `FamilyMember`, `LinkedAccount` ORM.
- **schemas/** — auth / family / linked-account / onboarding payloads.
- **routers/** — auth, onboarding, family, linked-accounts.
- **services/** — `auth_service`, `otp_service`, `user_service`, `user_context_loader`, `signup_notification_service`.

## Gotchas & invariants

- `user_context_loader.load_user_for_ai` eager-loads the whole AI user graph in one query (finance/risk/tax profiles, goals, portfolios + allocations/holdings, MF + cashflow rows) via `selectinload`. AI handlers rely on these being present — a new relationship an agent needs must be added here (`services/user_context_loader.py`).
- New-signup team notification fires from `/auth/signup` (`routers/auth_router.py`, `_maybe_notify_new_signup`) **the first time the user's identity (name + email) becomes complete** — i.e. the moment they submit the name/PIN/email setup page, *before* CAMS/onboarding. The `/auth/me` safety-net call the setup page makes also fires it, guarded by the same name+email False→True transition, so each user pings **exactly once** regardless of which call lands the identity (idempotent re-calls & later profile edits don't re-ping). `/onboarding/complete` no longer notifies. Channels are Slack (`SLACK_SIGNUP_WEBHOOK_URL`) + optional Google Sheet (`SIGNUP_SHEET_WEBHOOK_URL`); fired as a best-effort background task that swallows errors so an outage never fails the request (`services/signup_notification_service.py`).

## Don't read

- `__pycache__/`.
