"""OpenAPI tag metadata for the FastAPI app (``openapi_tags`` in ``app.main``)."""

from __future__ import annotations

from typing import Any

OPENAPI_TAG_METADATA: list[dict[str, Any]] = [
    {"name": "Health", "description": "API info, health checks, and deploy metadata."},
    {"name": "Auth", "description": "Registration, login, JWT tokens, and account updates."},
    {"name": "Onboarding", "description": "Early onboarding profile and other assets."},
    {"name": "Profile", "description": "Complete profile, risk, tax, and investment preferences."},
    {"name": "Goals", "description": "Financial goals, contributions, and holdings."},
    {"name": "Portfolio", "description": "Portfolios, allocations, holdings, and Finvu sync."},
    {"name": "Chat", "description": "Chat sessions, messages, and statement uploads."},
    {"name": "Meeting Notes", "description": "Advisor meeting notes and line items."},
    {"name": "Notifications", "description": "User notifications and alerts."},
    {"name": "Discovery", "description": "Fund discovery and filters."},
    {"name": "Rebalancing", "description": "Rebalancing recommendations."},
    {"name": "Investment Policy Statement", "description": "Investment policy statements."},
    {"name": "Linked Accounts", "description": "Linked external accounts."},
    {"name": "Family", "description": "Family linking and acting as a member."},
    {"name": "SimBanks", "description": "SimBanks ConnectHub sync."},
    {"name": "MF Data", "description": "Mutual fund tables: catalog, NAV, SIPs, ledger, snapshots, AA imports."},
    {"name": "MF Ingest", "description": "Mutual fund reference data ingestion."},
    {"name": "AI — Intent classifier", "description": "Direct intent classification endpoint."},
    {"name": "AI — Market commentary", "description": "Market commentary generation."},
    {"name": "AI — Portfolio query", "description": "Portfolio Q&A agent."},
    {"name": "AI — Asset allocation", "description": "Asset allocation recommendations."},
    {"name": "AI — Drift analyzer", "description": "Drift analyzer (stub / status)."},
    {"name": "AI — Mutual fund status", "description": "Mutual fund status (stub)."},
    {"name": "AI — Risk profile", "description": "Risk profile module (stub / planned)."},
]
