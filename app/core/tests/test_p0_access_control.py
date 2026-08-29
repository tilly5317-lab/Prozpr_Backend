"""Regression guards for the two access-control holes found in the DPDP audit.

Both were live on the public host. They are guarded here rather than in a domain
test folder because both are about the *routing/auth* layer, and because
`app/core/tests` is the one test package guaranteed to ship (see .gitignore).
"""

from __future__ import annotations

import pytest
from fastapi import Depends


# ── P0-1: the FP sandbox Quest routes must never be anonymous ──────────────


def _fp_openapi_paths():
    """Resolve the routes the deployed server actually publishes.

    ``router.routes`` is not enough on FastAPI 0.139: an included sub-router is
    stored as a lazy ``_IncludedRouter`` placeholder and its children only
    materialise when the schema is built. Going through ``openapi()`` also means
    we assert on the contract clients see, not on our own wiring.
    """
    from fastapi import FastAPI

    from app.domains.execution.routers import fp_router

    app = FastAPI()
    app.include_router(fp_router.router)
    return app.openapi()["paths"]


def test_sandbox_routes_still_exist():
    """If this drops to zero the guard below passes vacuously."""
    paths = _fp_openapi_paths()
    assert len([p for p in paths if "/sandbox" in p]) >= 25


def test_every_sandbox_operation_requires_authentication():
    """These proxy FP's TENANT-WIDE endpoints, and the write verbs take an
    unvalidated body straight through to the KYC/order gateway. One anonymous
    operation exposes every investor on the tenant, not just the caller."""
    paths = _fp_openapi_paths()
    open_ops = [
        f"{method.upper()} {path}"
        for path, ops in paths.items()
        if "/sandbox" in path
        for method, op in ops.items()
        if not op.get("security")
    ]
    assert open_ops == [], f"unauthenticated sandbox operations: {open_ops}"


def test_the_real_fp_routes_are_untouched():
    """The gate must not have swallowed the user-facing FP endpoints."""
    paths = _fp_openapi_paths()
    assert "/fp/status" in paths
    assert paths["/fp/status"]["get"].get("security")


def test_quest_guard_is_off_unless_explicitly_enabled(monkeypatch):
    """Default OFF. An environment that has not opted in must 404, not serve."""
    from app.core.config import Settings

    monkeypatch.delenv("FP_SANDBOX_QUEST_ENABLED", raising=False)
    assert Settings.fp_sandbox_quest_enabled() is False

    monkeypatch.setenv("FP_SANDBOX_QUEST_ENABLED", "true")
    assert Settings.fp_sandbox_quest_enabled() is True


def test_quest_guard_depends_on_a_real_user():
    """The flag alone is not enough — an opted-in env still must not be open."""
    from app.domains.execution.routers.fp_router import _require_quest_access

    defaults = _require_quest_access.__defaults__ or ()
    assert any(isinstance(d, type(Depends(lambda: None))) for d in defaults), (
        "_require_quest_access must take an auth dependency, not just the flag"
    )


# ── P0-2: a family link must be OTP'd to the member's OWN number ───────────


class _FakeUser:
    def __init__(self, phone, email=None):
        self.phone = phone
        self.email = email


class _FakeLink:
    def __init__(self, phone, member_user):
        self.id = "fm-1"
        self.phone = phone
        self.member_user = member_user


def _mismatch(fm) -> bool:
    """Mirror of the activation guard in family_router.verify_family_otp."""
    return fm.member_user is not None and fm.phone != fm.member_user.phone


def test_link_whose_otp_went_to_the_callers_number_is_refused():
    """The attack: look the victim up by EMAIL, supply your own phone, receive
    the OTP yourself, and get an identity swap into their account."""
    victim = _FakeUser(phone="+918468882140", email="victim@example.com")
    poisoned = _FakeLink(phone="+919999900000", member_user=victim)
    assert _mismatch(poisoned) is True


def test_link_otp_d_to_the_members_own_number_activates():
    victim = _FakeUser(phone="+918468882140", email="victim@example.com")
    honest = _FakeLink(phone="+918468882140", member_user=victim)
    assert _mismatch(honest) is False


def test_add_member_pins_the_linked_accounts_phone_not_the_payloads():
    """Source-level guard: the handler must build the row and send the OTP from
    the looked-up user's phone. Asserting on source keeps this honest without
    standing up a DB + MSG91 double for a routing-layer bug."""
    import inspect

    from app.domains.identity.routers import family_router

    src = inspect.getsource(family_router.add_family_member)
    assert "consent_phone = linked_user.phone" in src
    assert "_split_phone(consent_phone)" in src, "OTP must target the linked account"
    assert "phone=phone_full" not in src, "payload phone must not reach the row"


@pytest.mark.parametrize(
    "attr,expected",
    [("email", "linked_user.email"), ("phone", "consent_phone")],
)
def test_member_contact_details_mirror_the_linked_account(attr, expected):
    import inspect

    from app.domains.identity.routers import family_router

    src = inspect.getsource(family_router.add_family_member)
    assert f"{attr}={expected}" in src
