"""The ideal allocation engine is Prozpr's recommendation — preference-free
(spec 2026-09-14). Customer preferences are honoured only by the practical
(holdings-aware) engine. Guard against the post-hoc override creeping back."""

from __future__ import annotations

from pathlib import Path

_SERVICE = Path(__file__).resolve().parents[1] / "service.py"


def test_ideal_engine_service_does_not_apply_a_preference():
    src = _SERVICE.read_text()
    assert "apply_human_override" not in src, (
        "aa_engine/service.py must not reshape the ideal output with a "
        "customer preference — that belongs to the practical engine"
    )
    assert "load_human_override_for_user" not in src
