"""Guards for EncryptedJSON — the first real user of ENCRYPTION_KEY."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from app.core.encrypted_types import _PREFIX, EncryptedJSON


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())


FP_ACCOUNT_BLOB = {
    "pan": "ABCDE1234F",
    "date_of_birth": "1990-04-11",
    "gender": "female",
    "bank_account": {"account_number": "50100234567890", "ifsc": "HDFC0001234"},
}


def test_round_trips_through_the_column():
    col = EncryptedJSON()
    stored = col.process_bind_param(FP_ACCOUNT_BLOB, None)
    assert col.process_result_value(stored, None) == FP_ACCOUNT_BLOB


def test_stored_form_leaks_nothing_readable():
    """This is the whole point: a DB dump or an RDS snapshot must not contain
    the PAN, the account number or the IFSC in the clear."""
    stored = EncryptedJSON().process_bind_param(FP_ACCOUNT_BLOB, None)
    assert stored.startswith(_PREFIX)
    for secret in ("ABCDE1234F", "50100234567890", "HDFC0001234", "1990-04-11"):
        assert secret not in stored


def test_none_stays_none():
    col = EncryptedJSON()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


def test_plaintext_rows_written_before_the_migration_still_read():
    """Deploying this onto a live table must not 500 every historical row."""
    col = EncryptedJSON()
    legacy = json.dumps({"state": "confirmed"})
    assert col.process_result_value(legacy, None) == {"state": "confirmed"}
    # Postgres JSON columns hand back a decoded object, not a string.
    assert col.process_result_value({"state": "confirmed"}, None) == {
        "state": "confirmed"
    }


def test_unreadable_ciphertext_degrades_instead_of_exploding(monkeypatch):
    """After a key rotation the blob is lost, but the order's own columns carry
    everything the app needs — a raised exception here would break order reads."""
    col = EncryptedJSON()
    stored = col.process_bind_param({"a": 1}, None)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert col.process_result_value(stored, None) is None


def test_write_refuses_rather_than_silently_storing_plaintext(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(Exception):
        EncryptedJSON().process_bind_param(FP_ACCOUNT_BLOB, None)
