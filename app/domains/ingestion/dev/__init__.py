"""DEV-ONLY helpers for the ingestion domain.

Nothing here is imported by a production code path. Modules in this package are
reached only behind an explicit dev gate (today: ``Settings.mfc_mock_enabled``),
which is why they may pull in heavier dependencies or write files on import that
the rest of the domain must not.
"""
