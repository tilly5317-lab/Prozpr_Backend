"""The forgot-PIN resend cooldown.

`/auth/pin-reset/request` is unauthenticated by necessity — the whole point is
that the caller cannot sign in — so the cooldown is the only thing standing
between someone who knows a registered number and an inbox full of codes. It
also protects the mail quota.

Only the window is exercised here: it is a pure function precisely so this needs
no database, no app fixture and no clock patching.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domains.identity.routers.auth_router import (
    _PIN_RESET_RESEND_COOLDOWN_S,
    _PIN_RESET_TTL_MINUTES,
    _within_resend_cooldown,
)

NOW = datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)


def _expiry_for_code_issued_ago(seconds: float) -> datetime:
    """The expiry column as it would read for a code sent `seconds` ago."""
    return NOW - timedelta(seconds=seconds) + timedelta(minutes=_PIN_RESET_TTL_MINUTES)


def test_no_previous_code_is_never_throttled():
    """A first-time request must always go through."""
    assert _within_resend_cooldown(None, NOW) is False


def test_code_just_sent_is_throttled():
    assert _within_resend_cooldown(_expiry_for_code_issued_ago(1), NOW) is True


def test_throttled_right_up_to_the_boundary():
    just_inside = _PIN_RESET_RESEND_COOLDOWN_S - 1
    assert _within_resend_cooldown(_expiry_for_code_issued_ago(just_inside), NOW) is True


def test_allowed_once_the_cooldown_has_passed():
    just_outside = _PIN_RESET_RESEND_COOLDOWN_S + 1
    assert (
        _within_resend_cooldown(_expiry_for_code_issued_ago(just_outside), NOW) is False
    )


def test_a_long_dead_code_does_not_block_a_new_one():
    """An expiry from hours ago is stale, not a throttle — a user who ignored a
    code yesterday must still be able to ask for one today."""
    stale = NOW - timedelta(hours=6)
    assert _within_resend_cooldown(stale, NOW) is False


def test_naive_timestamps_do_not_raise():
    """SQLite hands back naive datetimes. Subtracting one from an aware `now`
    raises TypeError, which would surface as a 500 on the reset endpoint."""
    naive = _expiry_for_code_issued_ago(1).replace(tzinfo=None)
    assert _within_resend_cooldown(naive, NOW) is True
