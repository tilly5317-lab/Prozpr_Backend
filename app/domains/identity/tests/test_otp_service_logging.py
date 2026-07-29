"""PII contract: OTP logs must never carry a full phone number."""

from app.domains.identity.services import otp_service


def test_mask_number_keeps_only_last_four():
    assert otp_service._mask_number("919876543210") == "*****3210"


def test_mask_number_handles_short_input():
    assert otp_service._mask_number("12") == "*****"


def test_mask_number_handles_empty():
    assert otp_service._mask_number("") == "*****"
