"""Pydantic schema — `early_access.py`.

Request/response shapes for the public `/early-access` endpoints. There is no
ORM model behind these: the register is a Google Sheet, not a table, and the
seat count is read back out of that Sheet rather than kept anywhere here (see
``services/early_access_service.py``).

The field names mirror the `/earlyaccess` page's form exactly — this is the
agreed contract with the frontend, so rename on neither side alone.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# What the profession dropdown offers. Free text is NOT accepted: the page
# renders a fixed list, so anything else is a caller that bypassed the form,
# and a tidy column is worth more to the team working the list than an open
# string would be. "Other" is the escape hatch, and is on the list.
PROFESSIONS = (
    "Finance",
    "Tech",
    "HR",
    "Management",
    "Consulting",
    "Healthcare",
    "Legal",
    "Business owner",
    "Student",
    "Other",
)

# WhatsApp numbers are Indian national numbers, exactly like app accounts —
# ten digits behind a +91 (mirrors MOBILE_DIGITS in `identity/schemas/auth.py`).
# The page shows +91 as a fixed prefix and takes ten digits, so a number can
# only ever be stored in one shape and the team can dial the column directly.
WHATSAPP_DIGITS = 10
WHATSAPP_COUNTRY_CODE = "+91"

# Punctuation someone might reasonably type or paste. Letters are refused
# outright rather than stripped — quietly dropping characters turns a typo into
# a different, valid-looking number.
_PHONE_PUNCTUATION = frozenset(" -().+")


class EarlyAccessSignupRequest(BaseModel):
    """One application from the public `/earlyaccess` page."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "Ananya Rao",
                    "email": "ananya@example.com",
                    "whatsapp": "+91 98765 43210",
                    "profession": "Finance",
                    "source": "earlyaccess_page",
                }
            ]
        }
    }

    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=320)
    whatsapp: str | None = Field(default=None, max_length=32)
    profession: str = Field(..., min_length=1, max_length=64)
    source: str = Field(default="earlyaccess_page", max_length=64)

    # Anti-spam honeypot: the form renders it visually hidden and leaves it
    # empty, so a bot that fills every input trips it.
    #
    # It used to be called `company`, which was a mistake that cost real
    # applicants. Chrome reads a field's name and label to decide what to
    # autofill, recognised "company" as the organization field, and filled it
    # from the visitor's saved profile — so anyone with autofill on was
    # silently discarded while being shown a success screen. The name must
    # therefore match NOTHING in a browser's profile vocabulary; a trap that
    # catches slightly fewer bots is a trade worth making against one that
    # drops real people.
    referrer_note: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        name = " ".join(v.split())
        if not name:
            raise ValueError("Please enter your name")
        return name

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        email = v.strip().lower()
        local, _, domain = email.partition("@")
        if (
            not local
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
        ):
            raise ValueError("Please enter a valid email address")
        return email

    @field_validator("whatsapp")
    @classmethod
    def validate_whatsapp(cls, v: str | None) -> str | None:
        """Normalise to ``+91XXXXXXXXXX``, or reject. Optional: blank is fine.

        A typed or pasted country code is tolerated so "+91 98765 43210",
        "919876543210" and "9876543210" all mean the same number — people
        paste from contacts, and refusing the form they already have is a lost
        lead for no reason. Anything that is not ten national digits is
        refused rather than truncated: storing a wrong number the team then
        tries to dial is worse than making someone retype it.
        """
        if v is None:
            return None
        raw = " ".join(v.split())
        if not raw:
            return None
        if any(not (c.isdigit() or c in _PHONE_PUNCTUATION) for c in raw):
            raise ValueError("WhatsApp number must contain digits only")
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) == WHATSAPP_DIGITS + 2 and digits.startswith("91"):
            digits = digits[2:]
        if len(digits) != WHATSAPP_DIGITS:
            raise ValueError(
                f"Please enter a {WHATSAPP_DIGITS}-digit WhatsApp number, "
                "without the country code"
            )
        return f"{WHATSAPP_COUNTRY_CODE}{digits}"

    @field_validator("profession")
    @classmethod
    def validate_profession(cls, v: str) -> str:
        choice = " ".join(v.split())
        match = {p.casefold(): p for p in PROFESSIONS}.get(choice.casefold())
        if not match:
            raise ValueError(
                "Please pick a profession from the list: " + ", ".join(PROFESSIONS)
            )
        return match

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        # Falls back rather than blanking: the Sheet's Source column is how the
        # team tells the landing page apart from any later campaign link, and a
        # blank there is worth less than a slightly wrong guess.
        return " ".join(v.split()) or "earlyaccess_page"

    @field_validator("referrer_note")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return " ".join(v.split()) or None


class SeatsResponse(BaseModel):
    """The live seat meter on the `/earlyaccess` page."""

    seats_total: int
    seats_claimed: int
    seats_left: int


class EarlyAccessSignupResponse(SeatsResponse):
    """Answer to a successful application.

    ``waitlisted`` is True once the claimed seats have passed the cap — the
    application is still recorded, it just sits behind the first hundred.
    ``already_registered`` means this email was on the list before today's
    submission; the page treats it as a success ("You're already on the list")
    rather than an error, so nothing about it is a failure path.
    """

    ok: bool = True
    waitlisted: bool = False
    already_registered: bool = False


class SignupStatusResponse(BaseModel):
    """Read by the app's public entry screen so it knows whether to offer
    account creation at all, and where to send someone who cannot create one."""

    signups_open: bool
    early_access_path: str = "/earlyaccess"
