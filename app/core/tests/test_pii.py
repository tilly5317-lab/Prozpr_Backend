"""Guards for the shared PII primitives.

The regression these exist for: PAN was scrubbed in one pipeline and readable in
another because each had its own copy of the patterns, and the copy that mattered
was upper-case only while the FP client lower-cases PAN before sending it.
"""

from __future__ import annotations

import pytest

from app.core.pii import (
    REDACTED,
    mask_account,
    mask_email,
    mask_mobile,
    mask_name,
    mask_pan,
    redact_obj,
    redact_text,
)


# ── masks ──────────────────────────────────────────────────────────────────


def test_mask_pan_keeps_only_the_last_four():
    masked = mask_pan("ABCDE1234F")
    assert masked == "XXXXXX234F"
    assert "ABCDE" not in masked


def test_mask_email_keeps_domain_for_recognition():
    assert mask_email("priya.sharma@gmail.com") == "pr***@gmail.com"


def test_mask_mobile_matches_the_shape_otp_logs_used():
    assert mask_mobile("+918468882140") == "*****2140"


def test_mask_account_and_name():
    assert mask_account("50100234567890") == "****7890"
    assert mask_name("Priya Sharma") == "P. S."


@pytest.mark.parametrize("fn", [mask_pan, mask_email, mask_mobile, mask_account, mask_name])
def test_masks_pass_through_empty(fn):
    assert fn(None) is None
    assert fn("") == ""


# ── text redaction ─────────────────────────────────────────────────────────


def test_pan_is_redacted_in_both_cases():
    """The lower-case arm is the one that leaked: fp_service lower-cases PAN."""
    assert "ABCDE1234F" not in redact_text("pan ABCDE1234F rejected")
    assert "abcde1234f" not in redact_text("pan abcde1234f rejected")


def test_email_mobile_ifsc_and_aadhaar_are_redacted():
    text = (
        "user priya@example.com on 9876543210 "
        "ifsc HDFC0001234 uid 1234 5678 9012"
    )
    out = redact_text(text)
    for leaked in ("priya@example.com", "9876543210", "HDFC0001234", "1234 5678 9012"):
        assert leaked not in out


def test_amounts_and_order_ids_survive():
    """Over-broad patterns make incidents unreadable; that is why they are narrow."""
    text = "order 4821 for INR 12,500.00 on scheme INF109K01Z48"
    assert redact_text(text) == text


# ── object redaction ───────────────────────────────────────────────────────


def test_sensitive_keys_are_redacted_even_when_the_value_looks_harmless():
    """A first name has no regex — only the key name gives it away."""
    out = redact_obj({"first_name": "Priya", "scheme": "ICICI Bluechip"})
    assert out["first_name"] == REDACTED
    assert out["scheme"] == "ICICI Bluechip"


def test_nested_third_party_error_body_is_swept():
    body = {
        "error": {
            "errors": [
                {"field": "pan", "message": "ABCDE1234F is already registered"},
                {"field": "bank_account", "message": "contact priya@example.com"},
            ]
        }
    }
    flat = repr(redact_obj(body))
    assert "ABCDE1234F" not in flat
    assert "priya@example.com" not in flat


def test_redaction_is_total_and_depth_capped():
    class Exploding:
        def __repr__(self):
            raise RuntimeError("boom")

    assert redact_obj(Exploding()) == REDACTED

    deep: dict = {}
    node = deep
    for _ in range(20):
        node["next"] = {}
        node = node["next"]
    node["pan"] = "ABCDE1234F"
    assert "ABCDE1234F" not in repr(redact_obj(deep))
