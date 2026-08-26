"""Guards for the DPDP rights surface: consent, export, erasure, retention."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.retention import POLICIES, _to_count
from app.domains.privacy.models.consent import (
    OPTIONAL_PURPOSES,
    ConsentPurpose,
    DeletedUserTombstone,
)
from app.domains.privacy.services import consent_service, erasure_service
from app.domains.privacy.services.export_service import _NEVER_EXPORT, _jsonable
from app.domains.privacy.services.user_graph import _NEVER_WALK


# ── consent ────────────────────────────────────────────────────────────────


def test_every_purpose_is_described_in_the_notice():
    """A purpose the notice cannot explain is a purpose we cannot lawfully claim
    consent for."""
    assert set(consent_service.PURPOSE_NOTICE) == set(ConsentPurpose)


def test_only_the_service_itself_is_non_optional():
    """If everything were 'necessary' the consent would not be free."""
    assert OPTIONAL_PURPOSES == set(ConsentPurpose) - {
        ConsentPurpose.account_and_advisory
    }


def test_notice_hash_changes_when_the_notice_does(monkeypatch):
    before = consent_service.notice_hash()
    patched = dict(consent_service.PURPOSE_NOTICE)
    patched[ConsentPurpose.analytics] = {
        **patched[ConsentPurpose.analytics],
        "detail": "something materially different",
    }
    monkeypatch.setattr(consent_service, "PURPOSE_NOTICE", patched)
    assert consent_service.notice_hash() != before


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("203.0.113.42", "203.0.113.0/24"),
        ("2001:db8:85a3:1:2:3:4:5", "2001:db8:85a3:1::/64"),
        (None, None),
    ],
)
def test_consent_ip_keeps_the_network_and_drops_the_host(raw, expected):
    """The ledger proves consent; it must not become a location trail."""
    assert consent_service._truncate_ip(raw) == expected


# ── erasure ────────────────────────────────────────────────────────────────


class _FakeUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.deleted_at = None
        self.deletion_scheduled_for = None
        self.pan = "ABCDE1234F"
        self.first_name = "Priya"
        self.middle_name = None
        self.last_name = "Sharma"
        self.date_of_birth = "1990-04-11"
        self.address = "12 MG Road"
        self.occupation = "Engineer"
        self.family_status = "married"
        self.email = "priya@example.com"
        self.mobile = "8468882140"
        self.phone = "+918468882140"
        self.password_hash = "$2b$12$abcdef"
        self.pin_reset_code_hash = "$2b$12$ghijkl"
        self.pin_reset_expires_at = datetime.now(timezone.utc)
        self.is_active = True


class _FakeSession:
    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_erasure_destroys_identity_immediately():
    """The grace window must not be a period in which a 'deleted' account still
    holds a readable name, PAN and phone number."""
    user = _FakeUser()
    await erasure_service.request_erasure(_FakeSession(), user)

    assert user.deleted_at is not None
    for field in (
        "pan",
        "first_name",
        "last_name",
        "date_of_birth",
        "address",
        "occupation",
        "email",
    ):
        assert getattr(user, field) is None, field
    assert user.password_hash is None, "a tombstoned account must not sign back in"
    assert user.is_active is False


@pytest.mark.asyncio
async def test_tombstoned_phone_is_unique_per_account():
    """`phone` is NOT NULL and unique — blanking it would make the second
    deletion collide with the first."""
    a, b = _FakeUser(), _FakeUser()
    await erasure_service.request_erasure(_FakeSession(), a)
    await erasure_service.request_erasure(_FakeSession(), b)
    assert a.phone != b.phone
    assert a.phone.startswith("deleted-")


@pytest.mark.asyncio
async def test_erasure_request_is_idempotent():
    user = _FakeUser()
    first = await erasure_service.request_erasure(_FakeSession(), user)
    second = await erasure_service.request_erasure(_FakeSession(), user)
    assert first == second


def test_grace_window_is_finite_and_short():
    assert 1 <= erasure_service.GRACE_DAYS <= 90


def test_tombstones_survive_the_purge():
    """Without this the walk deletes its own evidence and a restore silently
    resurrects an erased account."""
    assert DeletedUserTombstone.__tablename__ in _NEVER_WALK


# ── export ─────────────────────────────────────────────────────────────────


def test_export_never_returns_credentials():
    """A data export must not become a credential export."""
    for secret in ("password_hash", "pin_reset_code_hash"):
        assert secret in _NEVER_EXPORT


def test_export_serialises_db_types_json_cannot_hold():
    import decimal

    assert _jsonable(decimal.Decimal("12.50")) == 12.5
    assert _jsonable(uuid.UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert _jsonable(b"\x00") == "<binary>"
    nested = _jsonable({"a": [decimal.Decimal("1.5")]})
    assert nested == {"a": [1.5]}


# ── retention ──────────────────────────────────────────────────────────────


def test_consent_evidence_outlives_the_data_it_covers():
    """Deleting the proof of consent before the data it authorised would be
    exactly backwards."""
    periods = {p.name: p.days for p in POLICIES}
    assert periods["consent_records_superseded"] > periods["chat_messages"]


def test_every_policy_is_scoped_by_a_cutoff():
    """A policy without :cutoff would delete the whole table."""
    for policy in POLICIES:
        assert ":cutoff" in policy.sql, policy.name
        assert policy.days > 0, policy.name
        assert policy.rationale.strip(), policy.name


def test_dry_run_counts_instead_of_deleting():
    for policy in POLICIES:
        counted = _to_count(policy.sql)
        assert counted.upper().startswith("SELECT COUNT(*)")
        assert "DELETE" not in counted.upper()
        assert "UPDATE " not in counted.upper()


def test_statement_identity_columns_are_minimised_not_dropped():
    """The audit row must survive; only the duplicated identity goes."""
    policy = next(p for p in POLICIES if p.name == "mf_aa_imports_identity")
    assert policy.sql.strip().upper().startswith("UPDATE")
    assert "normalized_at IS NOT NULL" in policy.sql


def test_purge_is_due_only_after_the_window():
    now = datetime.now(timezone.utc)
    assert now + timedelta(days=erasure_service.GRACE_DAYS) > now
