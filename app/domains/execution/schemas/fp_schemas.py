"""Request/response schemas for FP order execution."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FpSetupRequest(BaseModel):
    """One-time account setup. PAN's 4th letter must be 'P' (individual) — the
    sandbox test PAN is ARRPP7775N. Email/mobile default from the user; the
    address defaults to a sandbox-safe constant."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Rahul Sharma",
                    "pan": "ARRPP7775N",
                    "date_of_birth": "1990-05-15",
                    "bank_account_number": "1234567890",
                    "bank_ifsc": "HDFC0000001",
                    "email": "rahul.sharma@example.com",
                    "mobile": "9876543210",
                }
            ]
        }
    )

    name: str = Field(max_length=255)
    pan: str = Field(min_length=10, max_length=10)
    date_of_birth: str = Field(description="YYYY-MM-DD")
    bank_account_number: Optional[str] = Field(default=None, max_length=30)
    bank_ifsc: Optional[str] = Field(default=None, max_length=15)
    email: Optional[str] = Field(default=None, max_length=320)
    mobile: Optional[str] = Field(default=None, max_length=20)


class FpKycRequest(BaseModel):
    """Run a KYC readiness check (Pre-Verification) on PAN + name + DOB."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Rahul Sharma",
                    "pan": "ARRPP7775N",
                    "date_of_birth": "1990-05-15",
                }
            ]
        }
    )

    name: str = Field(max_length=255)
    pan: str = Field(min_length=10, max_length=10)
    date_of_birth: str = Field(description="YYYY-MM-DD")


class FpKycResponse(BaseModel):
    """Summarised pre_verification result. ``status`` is FP's job state
    (accepted -> completed); per-field statuses are verified/failed once done."""

    id: str
    status: str
    verified: bool
    pan_status: Optional[str] = None
    pan_code: Optional[str] = None
    name_status: Optional[str] = None
    dob_status: Optional[str] = None
    readiness_status: Optional[str] = None
    readiness_code: Optional[str] = None


class FpKycSetupRequest(BaseModel):
    """The KYC page's single call: PAN from the user; name / DOB / email /
    mobile come from the user's identity on our backend. PAN is optional on
    re-poll (send ``{}`` or ``{"pan": null}`` to re-poll the stored PAN)."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"pan": "ARRPP7775N"}, {"pan": None}]}
    )

    pan: Optional[str] = Field(default=None, min_length=10, max_length=10)


class FpAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fp_investor_id: Optional[str] = None
    fp_investment_account_id: Optional[str] = None
    pan: Optional[str] = None
    holder_name: Optional[str] = None
    bank_account_masked: Optional[str] = None
    kyc_status: str
    kyc_pv_id: Optional[str] = None
    kyc_checked_at: Optional[datetime] = None
    created_at: datetime


class FpStatusResponse(BaseModel):
    configured: bool
    account: Optional[FpAccountResponse] = None
    kyc_complete: bool = False
    ready_to_transact: bool = False


class FpKycSetupResponse(BaseModel):
    """Combined result of the KYC page submit: the Pre-Verification summary
    plus the account row (setup runs automatically once KYC completes)."""

    kyc: Optional[FpKycResponse] = None
    account: FpAccountResponse
    ready_to_transact: bool = False


class FpLumpsumRequest(BaseModel):
    """One-time buy. ``scheme_code`` is an ISIN (INF…) or AMFI code — use a
    tenant-enabled one from ``GET /fp/fund-schemes`` or it 400s
    "scheme is not available for transaction"."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"scheme_code": "INF109K01423", "amount": 5000}]
        }
    )

    scheme_code: str = Field(max_length=40, description="AMFI code or ISIN")
    amount: float = Field(gt=0, description="Investment amount in INR")


class FpSipRequest(BaseModel):
    """Start a SIP. ``installment_day`` 1-28; ``number_of_installments`` is how
    many monthly buys to schedule."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scheme_code": "INF109K01423",
                    "amount": 5000,
                    "installment_day": 5,
                    "number_of_installments": 12,
                }
            ]
        }
    )

    scheme_code: str = Field(max_length=40, description="AMFI code or ISIN")
    amount: float = Field(gt=0, description="Monthly SIP amount in INR")
    installment_day: int = Field(default=5, ge=1, le=28, description="Day of month, 1-28")
    number_of_installments: int = Field(default=12, ge=1, le=1200)


# --- Sell / switch / SWP / STP (mapper Stage E-F) -------------------------
# NOTE: the request bodies these drive are modelled from FP's public docs.
# Unlike lumpsum/SIP (live-verified against the sandbox gateway), redemptions
# and switches were never order-tested on our tenant, and the sandbox
# settlement ceiling (pan_kyc_incomplete) blocks them regardless — treat these
# as shape-complete, pending-a-live-verification.


class FpRedemptionRequest(BaseModel):
    """Sell (redeem) from a holding — FP ``mf_redemptions``. Provide one of
    ``amount`` / ``units`` / ``all_units`` (full exit), plus the ``folio_number``
    to sell from — FP **requires** it (live-verified: it returns
    "folio_number: must not be null" otherwise).

    SANDBOX CAVEAT: no folio ever exists on this sandbox (a folio is only minted
    when a purchase settles, and nothing settles here), so a redemption cannot
    actually complete — this endpoint is shape-only until production."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scheme_code": "INF109K01423",
                    "amount": 2000,
                    "folio_number": "1234567890",
                },
                {
                    "scheme_code": "INF109K01423",
                    "all_units": True,
                    "folio_number": "1234567890",
                },
            ]
        }
    )

    scheme_code: str = Field(max_length=40, description="AMFI code or ISIN")
    amount: Optional[float] = Field(default=None, gt=0, description="INR to redeem")
    units: Optional[float] = Field(default=None, gt=0, description="Units to redeem")
    all_units: bool = Field(default=False, description="Full redemption (whole holding)")
    folio_number: str = Field(
        max_length=40,
        description="Folio to redeem from — FP requires this (no sandbox folios exist yet).",
    )


class FpSwitchRequest(BaseModel):
    """Move money scheme->scheme within one AMC — FP ``mf_switches``. One of
    ``amount`` / ``units`` / ``all_units``. Like a redemption it sells from a
    folio, so FP expects a ``folio_number``. SANDBOX CAVEAT: no folio exists
    here (nothing settles), so a switch cannot complete until production."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "from_scheme_code": "INF109K01423",
                    "to_scheme_code": "INF109KC1TY0",
                    "amount": 3000,
                    "folio_number": "1234567890",
                }
            ]
        }
    )

    from_scheme_code: str = Field(max_length=40, description="Out — AMFI/ISIN")
    to_scheme_code: str = Field(max_length=40, description="In — AMFI/ISIN")
    amount: Optional[float] = Field(default=None, gt=0, description="INR to switch")
    units: Optional[float] = Field(default=None, gt=0, description="Units to switch")
    all_units: bool = Field(default=False, description="Switch the whole holding")
    folio_number: Optional[str] = Field(default=None, max_length=40)


class FpRedemptionPlanRequest(BaseModel):
    """SWP — systematic withdrawal on a schedule (FP ``mf_redemption_plans``).
    No mandate needed (money flows out). Sells from a folio, so FP expects a
    ``folio_number``. SANDBOX CAVEAT: no folio exists here (nothing settles), so
    an SWP cannot complete until production."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scheme_code": "INF109K01423",
                    "amount": 2000,
                    "installment_day": 5,
                    "number_of_installments": 12,
                    "folio_number": "1234567890",
                }
            ]
        }
    )

    scheme_code: str = Field(max_length=40, description="AMFI code or ISIN")
    amount: Optional[float] = Field(default=None, gt=0, description="INR per withdrawal")
    units: Optional[float] = Field(default=None, gt=0, description="Units per withdrawal")
    installment_day: int = Field(default=5, ge=1, le=28, description="Day of month, 1-28")
    number_of_installments: int = Field(default=12, ge=1, le=1200)
    folio_number: Optional[str] = Field(default=None, max_length=40)


class FpSwitchPlanRequest(BaseModel):
    """STP — systematic transfer scheme->scheme on a schedule (FP
    ``mf_switch_plans``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "from_scheme_code": "INF109K01423",
                    "to_scheme_code": "INF109KC1TY0",
                    "amount": 3000,
                    "installment_day": 5,
                    "number_of_installments": 12,
                }
            ]
        }
    )

    from_scheme_code: str = Field(max_length=40, description="Out — AMFI/ISIN")
    to_scheme_code: str = Field(max_length=40, description="In — AMFI/ISIN")
    amount: float = Field(gt=0, description="INR per transfer")
    installment_day: int = Field(default=5, ge=1, le=28, description="Day of month, 1-28")
    number_of_installments: int = Field(default=12, ge=1, le=1200)


class FpFundScheme(BaseModel):
    """A fund that is transactable on the current FP tenant, enriched from our
    own scheme catalogue (``mf_fund_metadata``). ``fp_verified`` is only set
    when the endpoint was called with ``?verify=true`` (a live FP lookup)."""

    isin: str
    scheme_code: Optional[str] = None
    scheme_name: Optional[str] = None
    amc_name: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    plan_type: Optional[str] = None
    option_type: Optional[str] = None
    is_active: bool = True
    available_for_transaction: bool = True
    fp_verified: Optional[bool] = None
    fp_min_amount: Optional[float] = None
    note: Optional[str] = None


class FpFundSchemesResponse(BaseModel):
    """Active, transactable funds on the FP tenant. On sandbox this is the small
    tenant-enabled set (see ``Settings.get_fp_sandbox_schemes``); the full
    universe is a production-provisioning step with FP."""

    count: int
    verified: bool = False
    source: str
    schemes: List[FpFundScheme]


class FpFolio(BaseModel):
    """One folio (holding). ``simulated`` folios are priced with real NAVs from
    ``mf_nav_history`` where available."""

    folio_number: str
    isin: str
    scheme_code: Optional[str] = None
    scheme_name: Optional[str] = None
    amc_name: Optional[str] = None
    category: Optional[str] = None
    units: float
    avg_cost_nav: float
    current_nav: float
    nav_date: Optional[date] = None
    invested_amount: float
    current_value: float
    unrealized_gain: float
    unrealized_gain_pct: float


class FpFoliosResponse(BaseModel):
    """Investor folios. ``simulated=true`` means FP returned no folios (the
    sandbox never settles an order → no folio) and this is demo test data."""

    simulated: bool
    source: str
    note: Optional[str] = None
    count: int
    total_invested: float
    total_current_value: float
    total_unrealized_gain: float
    folios: List[FpFolio]


class FpReportSchemeLine(BaseModel):
    isin: str
    scheme_name: Optional[str] = None
    invested_amount: float
    current_value: float
    unrealized_gain: float
    unrealized_gain_pct: float
    xirr_pct: float


class FpCapitalGains(BaseModel):
    short_term_gain: float
    long_term_gain: float
    total_gain: float


class FpInvestmentReport(BaseModel):
    """Portfolio analytics — holdings summary + per-scheme returns + capital
    gains. ``simulated=true`` when built from test folios (sandbox reality)."""

    simulated: bool
    source: str
    note: Optional[str] = None
    as_of: datetime
    invested_amount: float
    current_value: float
    absolute_gain: float
    absolute_gain_pct: float
    xirr_pct: float
    cagr_pct: float
    schemes: List[FpReportSchemeLine]
    capital_gains: FpCapitalGains


class FpOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str
    scheme_code: Optional[str] = None
    isin: Optional[str] = None
    scheme_name: Optional[str] = None
    amount: float
    installment_day: Optional[int] = None
    number_of_installments: Optional[int] = None
    fp_id: str
    created_at: datetime


class FpRebalanceExecuteRequest(BaseModel):
    """Optional per-trade overrides from the order-review UI. ``amounts`` maps a
    **rebalancing-trade id (UUID)** to its amount in INR; a trade set to 0 (or
    negative) is skipped, and any trade you don't list keeps its computed
    amount. Omit the body (or send ``{"amounts": null}``) to place every pending
    BUY at its computed amount. The ``additionalPropN`` keys Swagger shows are
    placeholders — replace them with real trade ids from the latest run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"amounts": None},
                {
                    "amounts": {
                        "3f8a2c1e-9b7d-4a1e-8c2f-1a2b3c4d5e6f": 5000,
                        "9c2b7d10-4e3f-4c8a-b1d2-6e5f4a3b2c1d": 0,
                    }
                },
            ]
        }
    )

    amounts: Optional[dict[str, float]] = None


class FpOrderBatchResponse(BaseModel):
    """Result of a batch placement (SIP plan / rebalance buys)."""

    orders: List[FpOrderResponse]
    failed: List[str] = []
