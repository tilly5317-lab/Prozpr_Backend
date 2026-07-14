"""``execution`` domain — Fintech Primitives (Cybrilla) sandbox order execution.

Minimal, live-verified integration: one-time account setup (investor profile +
contact details + bank + MF investment account), a KYC readiness check
(Pre-Verification), and order placement (lumpsum ``mf_purchases`` + SIP
``mf_purchase_plans``). Every request body in this domain was confirmed against
the live ``prozpr`` sandbox — do not "fix" field names from the public docs,
which differ from the sandbox gateway.
"""
