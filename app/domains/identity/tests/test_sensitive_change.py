"""Step-up verification for sensitive edits — the parts worth pinning.

Deliberately unit-level. The router handlers need a DB session and a live
Resend key, but the pieces that decide *whether an identifier is disclosed* and
*whether a code is required at all* are pure, and those are the pieces where a
regression is a security bug rather than a broken screen.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domains.identity.routers.auth_router import (
    _bypasses_otp,
    _mask_email,
    _mask_pan,
)
from app.domains.identity.schemas.auth import (
    SensitiveChangeConfirmRequest,
    SensitiveChangeRequest,
)


class TestMaskPan:
    def test_keeps_only_the_recognisable_ends(self):
        # First five identify the holder to themselves; the four digits are the
        # part that makes the PAN usable against someone else's records.
        assert _mask_pan("ABCDE1234F") == "ABCDE••••F"

    def test_is_case_insensitive_on_input(self):
        assert _mask_pan("abcde1234f") == "ABCDE••••F"

    def test_none_stays_none(self):
        # "Not set" must stay distinguishable from "set but hidden".
        assert _mask_pan(None) is None

    def test_short_garbage_is_fully_masked(self):
        # A malformed value must never leak more than a well-formed one.
        assert _mask_pan("AB12") == "••••"

    @pytest.mark.parametrize("pan", ["ABCDE1234F", "ZZZZZ9999Z"])
    def test_never_reveals_the_digits(self, pan):
        masked = _mask_pan(pan)
        assert not any(c.isdigit() for c in masked)


class TestMaskEmail:
    def test_keeps_domain_and_first_last_of_local(self):
        assert _mask_email("jonathan@gmail.com") == "j••••••n@gmail.com"

    def test_two_character_local_part(self):
        assert _mask_email("jo@gmail.com") == "j•@gmail.com"


class TestOtpBypass:
    """The bypass is a hole by design; these tests pin where its edges are."""

    def test_closed_when_unset(self, monkeypatch):
        monkeypatch.delenv("OTP_BYPASS_DOMAINS", raising=False)
        _clear_settings_cache()
        assert _bypasses_otp("someone@prozpr.com") is False

    def test_opens_only_for_listed_domain(self, monkeypatch):
        monkeypatch.setenv("OTP_BYPASS_DOMAINS", "prozpr.com")
        _clear_settings_cache()
        assert _bypasses_otp("tester@prozpr.com") is True
        assert _bypasses_otp("victim@gmail.com") is False

    def test_does_not_match_on_suffix(self, monkeypatch):
        # `notprozpr.com` and `prozpr.com.evil.net` must NOT bypass — a naive
        # `endswith` check would let both through.
        monkeypatch.setenv("OTP_BYPASS_DOMAINS", "prozpr.com")
        _clear_settings_cache()
        assert _bypasses_otp("a@notprozpr.com") is False
        assert _bypasses_otp("a@prozpr.com.evil.net") is False

    def test_no_email_never_bypasses(self, monkeypatch):
        monkeypatch.setenv("OTP_BYPASS_DOMAINS", "prozpr.com")
        _clear_settings_cache()
        assert _bypasses_otp(None) is False


class TestSensitiveChangeRequestSchema:
    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            SensitiveChangeRequest(field="password_hash", new_value="x")

    def test_normalises_pan_to_upper(self):
        req = SensitiveChangeRequest(field="pan", new_value=" abcde1234f ")
        assert req.new_value == "ABCDE1234F"

    def test_rejects_malformed_pan_before_a_code_is_spent(self):
        # Caught at request time on purpose: rejecting at confirm would burn the
        # user's code over a typo.
        with pytest.raises(ValidationError):
            SensitiveChangeRequest(field="pan", new_value="ABCD1234F")

    def test_lowercases_email(self):
        req = SensitiveChangeRequest(field="email", new_value="  A@B.COM ")
        assert req.new_value == "a@b.com"

    def test_mobile_parks_country_code_with_the_number(self):
        # Confirm reads ONE column, so the parked value has to be
        # self-contained — a bare national number would be ambiguous.
        req = SensitiveChangeRequest(
            field="mobile", new_value="98765 43210", country_code="91"
        )
        assert req.new_value == "+91 9876543210"

    def test_mobile_defaults_to_india_when_no_code_given(self):
        req = SensitiveChangeRequest(field="mobile", new_value="9876543210")
        assert req.new_value == "+91 9876543210"

    def test_rejects_short_mobile(self):
        with pytest.raises(ValidationError):
            SensitiveChangeRequest(field="mobile", new_value="12345")

    def test_rejects_letters_in_mobile(self):
        # Silently stripping would turn a typo into a different real number.
        with pytest.raises(ValidationError):
            SensitiveChangeRequest(field="mobile", new_value="98765abcde")

    def test_rejects_malformed_email(self):
        with pytest.raises(ValidationError):
            SensitiveChangeRequest(field="email", new_value="not-an-email")


class TestConfirmSchema:
    def test_carries_only_the_code(self):
        # The pending value lives server-side; an intercepted confirm must not
        # be able to redirect the change.
        assert set(SensitiveChangeConfirmRequest.model_fields) == {"code"}

    def test_rejects_non_digits(self):
        with pytest.raises(ValidationError):
            SensitiveChangeConfirmRequest(code="12a456")

    def test_rejects_wrong_length(self):
        with pytest.raises(ValidationError):
            SensitiveChangeConfirmRequest(code="12345")


def _clear_settings_cache() -> None:
    """`get_settings` is lru_cached, so an env change is invisible without this."""
    from app.core.config import get_settings

    get_settings.cache_clear()
