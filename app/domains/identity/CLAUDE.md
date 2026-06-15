# app/domains/identity/ — user, auth, OTP, family members, linked accounts, onboarding

## Layers

- **models/** — `User`, `FamilyMember`, `LinkedAccount` ORM.
- **schemas/** — auth / family / linked-account / onboarding payloads.
- **routers/** — auth, onboarding, family, linked-accounts.
- **services/** — `auth_service`, `otp_service`, `user_service`, `user_context_loader`.

## Gotchas & invariants

- `user_context_loader.load_user_for_ai` eager-loads the whole AI user graph in one query (finance/risk/tax profiles, goals, portfolios + allocations/holdings, MF + cashflow rows) via `selectinload`. AI handlers rely on these being present — a new relationship an agent needs must be added here (`services/user_context_loader.py`).

## Don't read

- `__pycache__/`.
