"""End-to-end tests for the invite-only launch.

The Apps Script web app is stubbed with an in-memory sheet that honours the
same contract (append, dedupe-on-email, seat numbering, claimed count), so
these exercise the router and service wiring without a network call — and
pin the two behaviours that would be expensive to get wrong in production:
a lead silently lost, or a "you're on the list" shown when no row exists.
"""

from __future__ import annotations

import os

import pytest

import app.all_models  # noqa: F401  — register every ORM model before app.main
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domains.early_access.services import early_access_service as svc
from app.main import app

API = "/api/v1/early-access"

APPLICATION = {
    "name": "Ananya Rao",
    "email": "ananya@example.com",
    "whatsapp": "+91 98765 43210",
    "profession": "Finance",
    "source": "earlyaccess_page",
}


class FakeSheet:
    """Stands in for the Apps Script web app — see the script in CLAUDE.md."""

    def __init__(self) -> None:
        self.emails: list[str] = []

    def __call__(self, method: str, *, json=None, params=None) -> dict:
        if method == "GET":
            return {"ok": True, "claimed": len(self.emails)}
        email = json["email"]
        if email in self.emails:
            seat = self.emails.index(email) + 1
            return {
                "ok": True,
                "seat": seat,
                "claimed": len(self.emails),
                "already_registered": True,
            }
        self.emails.append(email)
        return {
            "ok": True,
            "seat": len(self.emails),
            "claimed": len(self.emails),
            "already_registered": False,
        }


@pytest.fixture
def sheet(monkeypatch: pytest.MonkeyPatch):
    """A client wired to a fresh in-memory sheet, with the caches cleared.

    Both caches are process-global, so leaving either populated would leak a
    seat count from one test into the next.
    """
    monkeypatch.setenv("EARLY_ACCESS_SHEET_WEBHOOK_URL", "https://script.example/exec")
    monkeypatch.setenv("EARLY_ACCESS_SEATS", "100")
    monkeypatch.delenv("SLACK_EARLY_ACCESS_WEBHOOK_URL", raising=False)
    fake = FakeSheet()
    monkeypatch.setattr(svc, "_call_sheet", fake)
    monkeypatch.setattr(svc, "_seats_cache", None, raising=False)
    svc._rate_hits.clear()
    yield fake
    svc._rate_hits.clear()


@pytest.fixture
def client(sheet) -> TestClient:
    return TestClient(app)


def test_application_takes_a_seat(client: TestClient, sheet: FakeSheet):
    r = client.post(f"{API}/signup", json=APPLICATION)
    assert r.status_code == 201
    assert r.json() == {
        "ok": True,
        "seats_total": 100,
        "seats_claimed": 1,
        "seats_left": 99,
        "waitlisted": False,
        "already_registered": False,
    }
    assert sheet.emails == ["ananya@example.com"]


def test_repeat_email_is_a_success_not_a_duplicate_row(
    client: TestClient, sheet: FakeSheet
):
    """The page renders this as "you're already on the list" — never an error,
    and never a second row for the same person."""
    client.post(f"{API}/signup", json=APPLICATION)
    r = client.post(
        f"{API}/signup", json={**APPLICATION, "email": "ANANYA@Example.com"}
    )
    assert r.status_code == 201
    assert r.json()["already_registered"] is True
    assert r.json()["seats_claimed"] == 1
    assert sheet.emails == ["ananya@example.com"]


def test_seat_meter_reads_the_sheet(client: TestClient):
    client.post(f"{API}/signup", json=APPLICATION)
    r = client.get(f"{API}/seats")
    assert r.status_code == 200
    assert r.json() == {"seats_total": 100, "seats_claimed": 1, "seats_left": 99}


def test_past_the_cap_is_waitlisted_not_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("EARLY_ACCESS_SEATS", "1")
    client.post(f"{API}/signup", json=APPLICATION)
    r = client.post(
        f"{API}/signup", json={**APPLICATION, "email": "second@example.com"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["waitlisted"] is True
    # Still recorded, and the meter never goes negative.
    assert body["seats_left"] == 0


def test_baseline_is_added_to_the_sheet_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Testers recruited before the page went up are real seats gone, so the
    meter has to include them or it understates how full the beta is."""
    monkeypatch.setenv("EARLY_ACCESS_SEATS_BASELINE", "60")
    client.post(f"{API}/signup", json=APPLICATION)
    r = client.get(f"{API}/seats")
    assert r.json() == {"seats_total": 100, "seats_claimed": 61, "seats_left": 39}


def test_baseline_defaults_to_zero_so_the_count_is_the_register(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Unset means the page reports exactly what the Sheet holds — no invented
    head start."""
    monkeypatch.delenv("EARLY_ACCESS_SEATS_BASELINE", raising=False)
    client.post(f"{API}/signup", json=APPLICATION)
    assert client.get(f"{API}/seats").json()["seats_claimed"] == 1


def test_baseline_pushes_the_beta_full_and_waitlists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """A beta that is already full must not keep handing out seats it lacks."""
    monkeypatch.setenv("EARLY_ACCESS_SEATS", "10")
    monkeypatch.setenv("EARLY_ACCESS_SEATS_BASELINE", "10")
    r = client.post(f"{API}/signup", json=APPLICATION)
    body = r.json()
    assert body["waitlisted"] is True
    assert body["seats_left"] == 0
    # The meter is clamped at the cap rather than reading "11 of 10".
    assert body["seats_claimed"] == 10


def test_honeypot_writes_nothing_and_reveals_nothing(
    client: TestClient, sheet: FakeSheet
):
    r = client.post(f"{API}/signup", json={**APPLICATION, "company": "Acme Corp"})
    assert r.status_code == 201  # a scraper learns nothing from the status
    assert sheet.emails == []  # …and no row was written
    assert r.json()["seats_claimed"] == 100  # nor anything about the real count


def test_rate_limit_after_a_burst(client: TestClient):
    for i in range(svc._RATE_LIMIT_MAX):
        assert (
            client.post(f"{API}/signup", json={**APPLICATION, "email": f"n{i}@x.com"})
        ).status_code == 201
    r = client.post(f"{API}/signup", json={**APPLICATION, "email": "over@x.com"})
    assert r.status_code == 429


def test_unreachable_sheet_is_503_never_a_false_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The Sheet is the sole register: if the row cannot be written, the
    applicant must be asked to retry, not told they are on the list."""

    def boom(*args, **kwargs):
        raise svc.EarlyAccessRegisterError("register down")

    monkeypatch.setattr(svc, "_call_sheet", boom)
    monkeypatch.setattr(svc, "_seats_cache", None, raising=False)
    assert client.post(f"{API}/signup", json=APPLICATION).status_code == 503
    # The meter errors too, rather than confidently rendering a wrong count.
    assert client.get(f"{API}/seats").status_code == 503


def test_unconfigured_webhook_is_503(monkeypatch: pytest.MonkeyPatch):
    """No `sheet` fixture here on purpose: this is the real service against an
    unset webhook, i.e. exactly what a deploy that forgot the env var does."""
    monkeypatch.delenv("EARLY_ACCESS_SHEET_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(svc, "_seats_cache", None, raising=False)
    svc._rate_hits.clear()
    r = TestClient(app).post(f"{API}/signup", json=APPLICATION)
    assert r.status_code == 503


@pytest.mark.parametrize(
    "bad",
    [
        {"email": "not-an-email"},
        {"profession": "Astronaut"},
        {"whatsapp": "call me maybe"},
        {"whatsapp": "12345"},
        {"name": "   "},
    ],
)
def test_invalid_input_is_422(client: TestClient, bad: dict):
    r = client.post(f"{API}/signup", json={**APPLICATION, **bad})
    assert r.status_code == 422


# ── The signup block ────────────────────────────────────────────────────────
def _can_sign_up(phone: str) -> bool:
    from app.domains.identity.routers.auth_router import _can_sign_up as fn

    return fn(phone)


def test_signups_closed_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SIGNUPS_OPEN", raising=False)
    monkeypatch.delenv("EARLY_ACCESS_ALLOWED_PHONES", raising=False)
    assert _can_sign_up("+919123456780") is False


def test_allowlist_lets_an_approved_number_through(monkeypatch: pytest.MonkeyPatch):
    """The formats differ on purpose — the team will paste these by hand."""
    monkeypatch.delenv("SIGNUPS_OPEN", raising=False)
    monkeypatch.setenv(
        "EARLY_ACCESS_ALLOWED_PHONES", " +91 98765-43210 , 919000000001 "
    )
    assert _can_sign_up("+919876543210") is True
    assert _can_sign_up("+919000000001") is True
    assert _can_sign_up("+919123456780") is False


def test_signups_open_reopens_for_everyone(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SIGNUPS_OPEN", "true")
    assert _can_sign_up("+919123456780") is True


def test_check_mobile_tells_an_unknown_number_it_cannot_sign_up(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("SIGNUPS_OPEN", raising=False)
    monkeypatch.delenv("EARLY_ACCESS_ALLOWED_PHONES", raising=False)
    r = client.post(
        "/api/v1/auth/check-mobile",
        json={"country_code": "+91", "mobile": "9123456780"},
    )
    assert r.status_code == 200
    assert r.json()["exists"] is False
    assert r.json()["can_sign_up"] is False


def test_settings_read_per_request_so_no_redeploy_is_needed(
    monkeypatch: pytest.MonkeyPatch,
):
    """`get_settings()` is cached, but these read os.environ each call — the
    whole point of the escape hatches is flipping them without a deploy."""
    settings = get_settings()
    monkeypatch.setenv("SIGNUPS_OPEN", "true")
    assert settings.signups_open() is True
    monkeypatch.setenv("SIGNUPS_OPEN", "false")
    assert settings.signups_open() is False


def test_seat_cap_falls_back_when_misconfigured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EARLY_ACCESS_SEATS", "not-a-number")
    assert get_settings().get_early_access_seats() == 100
    monkeypatch.setenv("EARLY_ACCESS_SEATS", "0")
    assert get_settings().get_early_access_seats() == 100


def test_env_example_documents_every_new_setting():
    """A setting nobody can find is a setting nobody sets."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    with open(os.path.join(root, ".env.example"), encoding="utf-8") as fh:
        env = fh.read()
    for key in (
        "SIGNUPS_OPEN",
        "EARLY_ACCESS_ALLOWED_PHONES",
        "EARLY_ACCESS_SEATS",
        "EARLY_ACCESS_SHEET_WEBHOOK_URL",
        "EARLY_ACCESS_SHEET_TOKEN",
        "EARLY_ACCESS_SEATS_BASELINE",
        "SLACK_EARLY_ACCESS_WEBHOOK_URL",
    ):
        assert key in env, f"{key} is missing from .env.example"
