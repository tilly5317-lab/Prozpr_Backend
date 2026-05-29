# app/domains/identity/ — user, auth, OTP, family members, linked accounts, onboarding

User, auth, otp, family members, linked accounts, onboarding.

## Layers

- **models/** — user, family_member, linked_account ORM
- **schemas/** — auth/family/linked_account/onboarding payloads
- **routers/** — auth/onboarding/family/linked_accounts routers
- **services/** — auth_service, otp_service, user_service, user_context_loader (loads the User graph for AI handlers)

## Don't read

- `__pycache__/`.
