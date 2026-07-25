"""Engine and app warning enums must not drift.

The engine emits `Rebalancing.models.WarningCode`; the persist service converts
it with `RebalancingWarningCode(warning.code.value)`
(`rebalancing_persist_service.py:239`), which raises `ValueError` on any member
the app side does not know. That escapes the whole chat turn *after* the engine
has already computed a plan, so a drifted enum is a 500, not a degraded reply.

This exact break shipped once: step2b added DEBT_SWITCH_SUPPRESSED engine-side
only, and every run where debt netting fired would have failed on persist.
"""

from __future__ import annotations

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.rebalancing.models.rebalancing_warning import (
    RebalancingWarningCode,
)

ensure_ai_agents_path()


def test_every_engine_warning_code_is_persistable():
    from Rebalancing.models import WarningCode  # type: ignore[import-not-found]

    engine = {c.value for c in WarningCode}
    app = {c.value for c in RebalancingWarningCode}
    missing = engine - app
    assert not missing, (
        f"engine emits warning code(s) the DB enum cannot store: {sorted(missing)}. "
        "Add them to RebalancingWarningCode AND ship an "
        "`ALTER TYPE rebalancing_warning_code_enum ADD VALUE` migration."
    )


def test_app_enum_has_no_codes_the_engine_never_emits():
    """Not fatal, but a stale member usually means a rename went half-done."""
    from Rebalancing.models import WarningCode  # type: ignore[import-not-found]

    engine = {c.value for c in WarningCode}
    app = {c.value for c in RebalancingWarningCode}
    assert not (app - engine), f"app-only warning codes: {sorted(app - engine)}"
