"""Application service — `simbanks_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from hashlib import sha256
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.linked_account import LinkedAccount, LinkedAccountStatus, LinkedAccountType
from app.models.mf import (
    MfFundMetadata,
    MfTransaction,
    MfTransactionSource,
    MfTransactionType,
    MfOptionType,
    MfPlanType,
)
from app.models.portfolio import Portfolio, PortfolioAllocation, PortfolioHistory, PortfolioHolding
from app.services.portfolio_service import get_or_create_primary_portfolio
from app.schemas.simbanks import SimBankDiscoveredAccount


SIMBANK_BASE = "https://simbanks.finfactor.in"
CONNECTHUB_BASE = f"{SIMBANK_BASE}/ConnectHub/AccountManagement"


@dataclass(frozen=True)
class _DepositSummary:
    current_balance: float
    currency: Optional[str]
    branch: Optional[str]
    facility: Optional[str]
    ifsc_code: Optional[str]
    masked_acc_number: Optional[str]


@dataclass(frozen=True)
class _MfHolding:
    scheme_code: str  # we map to amfiCode (<= 20 chars)
    instrument_name: str
    amc_name: str
    isin: Optional[str]
    scheme_option: Optional[str]
    scheme_plan: Optional[str]
    scheme_type: Optional[str]
    scheme_category: Optional[str]
    closing_units: float
    nav: float
    nav_date: Optional[str]
    folio_no: str


@dataclass(frozen=True)
class _MfTransactionRow:
    scheme_code: str  # amfiCode
    folio_number: str
    transaction_type: MfTransactionType
    transaction_date: datetime.date
    units: float
    nav: float
    amount: float


@dataclass(frozen=True)
class _EquityHolding:
    issuer_name: str
    isin: Optional[str]
    units: float
    last_traded_price: float
    # Some FIPs return MF schemes under FISchema/equities; detect via AMFI / scheme attrs.
    is_mutual_fund: bool = False
    scheme_type: Optional[str] = None
    scheme_category: Optional[str] = None


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _float_or_none(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_date_from_ms(epoch_ms: str) -> datetime.date:
    ms = int(float(epoch_ms))
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date()


def _parse_option_type(scheme_option: Optional[str]) -> MfOptionType:
    if not scheme_option:
        return MfOptionType.GROWTH
    s = scheme_option.upper()
    return MfOptionType.GROWTH if "GROWTH" in s else MfOptionType.IDCW


def _parse_plan_type(scheme_plan: Optional[str], scheme_code: str) -> MfPlanType:
    if scheme_plan:
        return MfPlanType.DIRECT if "DIRECT" in scheme_plan.upper() else MfPlanType.REGULAR
    # fall back to scheme_code string
    return MfPlanType.DIRECT if "DIRECT" in scheme_code.upper() else MfPlanType.REGULAR


def _classify_mf_bucket(scheme_type: Optional[str], scheme_category: Optional[str]) -> str:
    """Classify MF holdings into one of: Debt, Equity, Other.

    We use both scheme type and category labels from simulator payload.
    """
    raw = f"{scheme_type or ''} {scheme_category or ''}".upper()
    if any(k in raw for k in ("DEBT", "LIQUID", "MONEY MARKET", "GILT", "DURATION", "BOND")):
        return "Debt"
    if any(k in raw for k in ("EQUITY", "LARGE_CAP", "MID_CAP", "SMALL_CAP", "INDEX", "ELSS", "SECTORAL")):
        return "Equity"
    return "Other"


def _find_first_attrib(root: ET.Element, tag_endswith: str) -> dict[str, str]:
    for el in root.iter():
        if _strip_ns(el.tag) == tag_endswith:
            return dict(el.attrib)
    return {}


def _find_children(root: ET.Element, tag_endswith: str) -> list[ET.Element]:
    return [el for el in root.iter() if _strip_ns(el.tag) == tag_endswith]


def parse_deposit_account_xml(xml_text: str) -> tuple[_DepositSummary, dict[str, Any]]:
    root = ET.fromstring(xml_text)
    masked_acc_number = root.attrib.get("maskedAccNumber")
    linked_acc_ref = root.attrib.get("linkedAccRef")

    summary = None
    for el in root.iter():
        if _strip_ns(el.tag) == "Summary":
            summary = el.attrib
            break
    if not summary:
        raise ValueError("Deposit XML missing Summary")

    current_balance = float(summary.get("currentBalance") or 0.0)
    currency = summary.get("currency")

    branch = summary.get("branch")
    facility = summary.get("facility")
    ifsc_code = summary.get("ifscCode")

    summary_obj = _DepositSummary(
        current_balance=current_balance,
        currency=currency,
        branch=branch,
        facility=facility,
        ifsc_code=ifsc_code,
        masked_acc_number=masked_acc_number,
    )

    return summary_obj, {
        "linked_acc_ref": linked_acc_ref,
        "currency": currency,
        "branch": branch,
        "facility": facility,
        "ifsc_code": ifsc_code,
        "masked_acc_number": masked_acc_number,
    }


def parse_mutual_fund_account_xml(xml_text: str) -> tuple[dict[str, Any], list[_MfHolding], list[_MfTransactionRow]]:
    root = ET.fromstring(xml_text)
    masked_folio_no = root.attrib.get("maskedFolioNo")
    linked_acc_ref = root.attrib.get("linkedAccRef")

    summary_attrib = _find_first_attrib(root, "Summary")
    if not summary_attrib:
        raise ValueError("MF XML missing Summary")

    cost_value = _float_or_none(summary_attrib.get("costValue"))
    current_value = _float_or_none(summary_attrib.get("currentValue"))
    current_value = current_value if current_value is not None else 0.0

    holdings: list[_MfHolding] = []
    folio_by_amfi: dict[str, str] = {}

    for holding in _find_children(root, "Holding"):
        amfi_code = holding.attrib.get("amfiCode")
        if not amfi_code:
            continue
        scheme_code = str(amfi_code)[:20]
        folio_no = holding.attrib.get("folioNo") or masked_folio_no or ""
        folio_by_amfi[scheme_code] = folio_no

        closing_units = float(holding.attrib.get("closingUnits") or 0.0)
        nav = float(holding.attrib.get("nav") or 0.0)
        nav_date = holding.attrib.get("navDate")

        holdings.append(
            _MfHolding(
                scheme_code=scheme_code,
                instrument_name=holding.attrib.get("schemeCode") or "MF Scheme",
                amc_name=holding.attrib.get("amc") or "Unknown AMC",
                isin=holding.attrib.get("isin"),
                scheme_option=holding.attrib.get("schemeOption"),
                scheme_plan=None,  # plan is primarily from transactions; we can infer if needed
                scheme_type=holding.attrib.get("schemeTypes"),
                scheme_category=holding.attrib.get("schemeCategory"),
                closing_units=closing_units,
                nav=nav,
                nav_date=nav_date,
                folio_no=folio_no,
            )
        )

    txns: list[_MfTransactionRow] = []
    for txn in _find_children(root, "Transaction"):
        amfi_code = txn.attrib.get("amfiCode")
        if not amfi_code:
            continue
        scheme_code = str(amfi_code)[:20]

        txn_type_raw = (txn.attrib.get("type") or "").upper()
        try:
            txn_type = MfTransactionType(txn_type_raw)
        except ValueError:
            # best-effort mapping; ignore unsupported types
            continue

        transaction_date = _parse_date_from_ms(txn.attrib.get("transactionDate") or "0")

        amount = float(txn.attrib.get("amount") or 0.0)
        nav = float(txn.attrib.get("nav") or 0.0)

        units_raw = txn.attrib.get("units") or ""
        units = float(units_raw) if units_raw.strip() else 0.0
        if units <= 0 and nav > 0 and amount > 0:
            units = amount / nav

        folio_number = folio_by_amfi.get(scheme_code) or txn.attrib.get("folioNo") or masked_folio_no or ""
        if not folio_number:
            folio_number = "UNKNOWN"

        txns.append(
            _MfTransactionRow(
                scheme_code=scheme_code,
                folio_number=folio_number,
                transaction_type=txn_type,
                transaction_date=transaction_date,
                units=units,
                nav=nav,
                amount=amount,
            )
        )

    return (
        {
            "linked_acc_ref": linked_acc_ref,
            "masked_folio_no": masked_folio_no,
            "cost_value": cost_value,
            "current_value": current_value,
        },
        holdings,
        txns,
    )


def parse_equities_account_xml(xml_text: str) -> tuple[dict[str, Any], list[_EquityHolding]]:
    root = ET.fromstring(xml_text)
    masked_demat_id = root.attrib.get("maskedDematId") or root.attrib.get("maskedDematID")
    linked_acc_ref = root.attrib.get("linkedAccRef")

    summary_attrib = _find_first_attrib(root, "Summary")
    current_value = _float_or_none(summary_attrib.get("currentValue") if summary_attrib else None) or 0.0

    holdings: list[_EquityHolding] = []
    for h in _find_children(root, "Holding"):
        units = float(h.attrib.get("units") or 0.0)
        ltp = float(h.attrib.get("lastTradedPrice") or 0.0)
        issuer = (h.attrib.get("issuerName") or "Equity Holding").strip()
        amfi_code = (h.attrib.get("amfiCode") or h.attrib.get("amfi_code") or "").strip()
        scheme_cat = (h.attrib.get("schemeCategory") or "").strip() or None
        scheme_typ = (h.attrib.get("schemeTypes") or "").strip() or None
        is_mf = bool(amfi_code) or bool(scheme_cat) or bool(scheme_typ) or "FUND" in issuer.upper()
        holdings.append(
            _EquityHolding(
                issuer_name=issuer,
                isin=h.attrib.get("isin"),
                units=units,
                last_traded_price=ltp,
                is_mutual_fund=is_mf,
                scheme_type=scheme_typ,
                scheme_category=scheme_cat,
            )
        )

    return (
        {
            "linked_acc_ref": linked_acc_ref,
            "masked_demat_id": masked_demat_id,
            "current_value": current_value,
        },
        holdings,
    )


async def _simbanks_get_json(client: httpx.AsyncClient, path: str) -> Any:
    url = f"{CONNECTHUB_BASE}{path}"
    resp = await client.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


async def _simbanks_get_account_xml(client: httpx.AsyncClient, fip_id: str, account_ref_no: str) -> str:
    url = f"{CONNECTHUB_BASE}/Account/{fip_id}/{account_ref_no}"
    resp = await client.get(url, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    account_data = payload.get("accountData")
    if not account_data:
        raise ValueError(f"No accountData returned for {account_ref_no}")
    return account_data


async def discover_simbanks_accounts(mobile: str) -> list[SimBankDiscoveredAccount]:
    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        payload = await _simbanks_get_json(client, f"/Accounts/{mobile}")
        accounts = payload.get("accounts") or []

        discovered: list[SimBankDiscoveredAccount] = []
        for a in accounts:
            fip_id = str(a.get("fipId") or "")
            account_ref_no = str(a.get("accountRefNo") or "")
            fi_type = str(a.get("fiType") or "")
            account_type = str(a.get("accountType") or "")

            account_xml = await _simbanks_get_account_xml(client, fip_id=fip_id, account_ref_no=account_ref_no)

            # Decide kind from xml root attributes / namespace
            if "FISchema/deposit" in account_xml:
                kind = "deposit"
            elif "FISchema/mutual_funds" in account_xml:
                kind = "mutual_fund"
            elif "FISchema/equities" in account_xml or fi_type.upper() == "EQUITIES":
                kind = "equity"
            else:
                kind = "other"

            if kind == "deposit":
                summary_obj, _ = parse_deposit_account_xml(account_xml)
                discovered.append(
                    SimBankDiscoveredAccount(
                        account_ref_no=account_ref_no,
                        provider_name=fip_id,
                        fi_type=fi_type,
                        account_type=account_type,
                        kind="deposit",
                        masked_identifier=summary_obj.masked_acc_number,
                        currency=summary_obj.currency,
                        current_value=summary_obj.current_balance,
                        cost_value=None,
                        holdings_count=None,
                    )
                )
            elif kind == "mutual_fund":
                mf_summary, holdings, _ = parse_mutual_fund_account_xml(account_xml)
                discovered.append(
                    SimBankDiscoveredAccount(
                        account_ref_no=account_ref_no,
                        provider_name=fip_id,
                        fi_type=fi_type,
                        account_type=account_type,
                        kind="mutual_fund",
                        masked_identifier=mf_summary.get("masked_folio_no"),
                        currency="INR",
                        current_value=float(mf_summary.get("current_value") or 0.0),
                        cost_value=mf_summary.get("cost_value"),
                        holdings_count=len(holdings),
                    )
                )
            elif kind == "equity":
                eq_summary, eq_holdings = parse_equities_account_xml(account_xml)
                discovered.append(
                    SimBankDiscoveredAccount(
                        account_ref_no=account_ref_no,
                        provider_name=fip_id,
                        fi_type=fi_type,
                        account_type=account_type,
                        kind="equity",
                        masked_identifier=eq_summary.get("masked_demat_id"),
                        currency="INR",
                        current_value=float(eq_summary.get("current_value") or 0.0),
                        cost_value=None,
                        holdings_count=len(eq_holdings),
                    )
                )

        # SimBanks can return many cloned records for a single mobile.
        # Collapse obvious duplicates so onboarding UI stays usable.
        deduped: list[SimBankDiscoveredAccount] = []
        seen: set[tuple[str, str, float, float, int]] = set()
        for acc in discovered:
            fp = (
                acc.kind,
                (acc.masked_identifier or "").strip(),
                round(float(acc.current_value or 0.0), 2),
                round(float(acc.cost_value or 0.0), 2),
                int(acc.holdings_count or 0),
            )
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append(acc)

        return deduped


async def sync_simbanks_accounts(
    db: AsyncSession,
    user: Any,
    accepted_account_ref_nos: list[str],
) -> tuple[Portfolio, list[uuid.UUID]]:
    """Fetch accepted accounts from SimBanks, parse ReBIT XML, and refresh the user's portfolio."""
    mobile = user.mobile

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        payload = await _simbanks_get_json(client, f"/Accounts/{mobile}")
        accounts = payload.get("accounts") or []

        accepted = [a for a in accounts if str(a.get("accountRefNo")) in set(accepted_account_ref_nos)]
        if not accepted:
            raise ValueError("No matching SimBanks accounts for accepted refs")

        discovered_pairs: list[tuple[str, str, str, str, str, str]] = []
        # fip_id, account_ref_no, kind, fi_type, account_type, account_xml
        for a in accepted:
            account_ref_no = str(a.get("accountRefNo") or "")
            fip_id = str(a.get("fipId") or "")
            fi_type = str(a.get("fiType") or "")
            account_type = str(a.get("accountType") or "")
            account_xml = await _simbanks_get_account_xml(client, fip_id=fip_id, account_ref_no=account_ref_no)
            if "FISchema/deposit" in account_xml:
                kind = "deposit"
            elif "FISchema/mutual_funds" in account_xml:
                kind = "mutual_fund"
            elif "FISchema/equities" in account_xml or fi_type.upper() == "EQUITIES":
                kind = "equity"
            else:
                kind = "other"
            discovered_pairs.append((fip_id, account_ref_no, kind, fi_type, account_type, account_xml))

    # DB refresh (transactionally)
    portfolio = await get_or_create_primary_portfolio(db, user.id)
    portfolio_id = portfolio.id

    await db.execute(delete(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio_id))
    await db.execute(delete(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio_id))
    await db.execute(delete(PortfolioHistory).where(PortfolioHistory.portfolio_id == portfolio_id))
    await db.execute(delete(MfTransaction).where(MfTransaction.user_id == user.id))
    await db.execute(
        delete(LinkedAccount).where(
            LinkedAccount.user_id == user.id,
            LinkedAccount.account_type.in_(
                [
                    LinkedAccountType.bank_account,
                    LinkedAccountType.mutual_fund,
                    LinkedAccountType.stock_demat,
                ]
            ),
        )
    )

    linked_account_ids: list[uuid.UUID] = []

    total_value = 0.0
    total_invested = 0.0

    # Canonical portfolio buckets requested by product:
    # Cash, Debt, Equity, Other
    bucket_amounts: dict[str, float] = {
        "Cash": 0.0,
        "Debt": 0.0,
        "Equity": 0.0,
        "Other": 0.0,
    }

    holdings_to_create: list[PortfolioHolding] = []
    allocations_to_create: list[PortfolioAllocation] = []

    # MF data buffers
    mf_txns_to_create: list[MfTransaction] = []
    mf_fund_metadata_to_upsert: dict[str, dict[str, Any]] = {}

    for fip_id, account_ref_no, kind, fi_type, account_type, account_xml in discovered_pairs:
        if kind == "deposit":
            summary_obj, extra = parse_deposit_account_xml(account_xml)
            current_balance = summary_obj.current_balance
            bucket_amounts["Cash"] += current_balance
            total_value += current_balance
            total_invested += current_balance

            inst_name = (summary_obj.branch or "Bank Account").strip()
            if summary_obj.facility:
                inst_name = f"{inst_name} [{summary_obj.facility}]"

            holding = PortfolioHolding(
                portfolio_id=portfolio_id,
                instrument_name=inst_name[:255],
                instrument_type="bank_account",
                ticker_symbol=(summary_obj.ifsc_code or "").strip()[:20] or None,
                quantity=None,
                average_cost=None,
                current_price=None,
                current_value=current_balance,
                allocation_percentage=None,
                exchange=None,
                expense_ratio=None,
                return_1y=None,
                return_3y=None,
                return_5y=None,
            )
            holdings_to_create.append(holding)

            linked = LinkedAccount(
                user_id=user.id,
                account_type=LinkedAccountType.bank_account,
                provider_name=fip_id,
                account_identifier=account_ref_no,
                encrypted_access_token=None,
                status=LinkedAccountStatus.active,
                metadata_json={
                    "fi_type": fi_type,
                    "account_type": account_type,
                    "portfolio_bucket": "Cash",
                    **extra,
                },
                linked_at=datetime.now(timezone.utc),
                last_synced_at=datetime.now(timezone.utc),
            )
            db.add(linked)
            await db.flush()
            linked_account_ids.append(linked.id)

        elif kind == "mutual_fund":
            mf_summary, holdings, txns = parse_mutual_fund_account_xml(account_xml)
            mf_current_value = float(mf_summary.get("current_value") or 0.0)
            mf_cost = mf_summary.get("cost_value") or 0.0

            total_value += mf_current_value
            total_invested += float(mf_cost)

            # Portfolio holdings rows
            mf_bucket_total = 0.0
            for h in holdings:
                current_val = h.closing_units * h.nav
                bucket = _classify_mf_bucket(h.scheme_type, h.scheme_category)
                bucket_amounts[bucket] += current_val
                mf_bucket_total += current_val
                holdings_to_create.append(
                    PortfolioHolding(
                        portfolio_id=portfolio_id,
                        instrument_name=h.instrument_name[:255],
                        instrument_type="mutual_fund",
                        ticker_symbol=(h.isin or h.scheme_code)[:20] or None,
                        quantity=h.closing_units,
                        average_cost=None,
                        current_price=h.nav,
                        current_value=current_val,
                        allocation_percentage=None,
                        exchange=None,
                        expense_ratio=None,
                        return_1y=None,
                        return_3y=None,
                        return_5y=None,
                    )
                )

            # Simulator MF summaries can include current value that does not exactly
            # match (or sometimes exceeds) sum(closingUnits * nav). Keep category
            # buckets aligned with total portfolio value by assigning the remainder.
            if mf_current_value > mf_bucket_total:
                remainder = mf_current_value - mf_bucket_total
                if holdings:
                    inferred_bucket = _classify_mf_bucket(
                        holdings[0].scheme_type, holdings[0].scheme_category
                    )
                else:
                    inferred_bucket = "Other"
                bucket_amounts[inferred_bucket] += remainder

            # Upsert MF metadata + transactions
            # We'll use scheme_code=amfiCode for scheme FK keys.
            for h in holdings:
                mf_fund_metadata_to_upsert[h.scheme_code] = {
                    "scheme_code": h.scheme_code,
                    "scheme_name": h.instrument_name[:200],
                    "amc_name": h.amc_name[:100],
                    "category": (h.scheme_category or "Equity")[:50],
                    "sub_category": h.scheme_type[:100] if h.scheme_type else None,
                    "plan_type": _parse_plan_type(h.scheme_plan, h.scheme_code),
                    "option_type": _parse_option_type(h.scheme_option),
                }

            # MF transactions
            for t in txns:
                # If units are 0 (missing in XML), avoid creating invalid rows.
                if t.units <= 0:
                    continue
                raw_key = "|".join(
                    [
                        str(user.id),
                        t.scheme_code,
                        t.folio_number[:30],
                        t.transaction_type.value,
                        t.transaction_date.isoformat(),
                        f"{t.units:.6f}",
                        f"{t.nav:.6f}",
                        f"{t.amount:.2f}",
                    ]
                )
                mf_txns_to_create.append(
                    MfTransaction(
                        user_id=user.id,
                        scheme_code=t.scheme_code,
                        sip_mandate_id=None,
                        folio_number=t.folio_number[:30],
                        transaction_type=t.transaction_type,
                        transaction_date=t.transaction_date,
                        units=t.units,
                        nav=t.nav,
                        amount=t.amount,
                        stamp_duty=None,
                        source_system=MfTransactionSource.SIMBANKS,
                        source_import_id=None,
                        source_txn_fingerprint=sha256(raw_key.encode("utf-8")).hexdigest(),
                    )
                )

            linked = LinkedAccount(
                user_id=user.id,
                account_type=LinkedAccountType.mutual_fund,
                provider_name=fip_id,
                account_identifier=account_ref_no,
                encrypted_access_token=None,
                status=LinkedAccountStatus.active,
                metadata_json={
                    "fi_type": fi_type,
                    "account_type": account_type,
                    "portfolio_bucket": "MixedMF",  # per-holding bucket in portfolio_holdings
                    "masked_folio_no": mf_summary.get("masked_folio_no"),
                    "cost_value": mf_cost,
                    "current_value": mf_current_value,
                },
                linked_at=datetime.now(timezone.utc),
                last_synced_at=datetime.now(timezone.utc),
            )
            db.add(linked)
            await db.flush()
            linked_account_ids.append(linked.id)
        elif kind == "equity":
            eq_summary, eq_holdings = parse_equities_account_xml(account_xml)
            eq_current_value = float(eq_summary.get("current_value") or 0.0)

            total_value += eq_current_value
            total_invested += eq_current_value

            eq_holdings_total = 0.0
            for h in eq_holdings:
                current_val = h.units * h.last_traded_price
                eq_holdings_total += current_val
                if h.is_mutual_fund:
                    mf_bucket = _classify_mf_bucket(h.scheme_type, h.scheme_category)
                    bucket_amounts[mf_bucket] += current_val
                    holdings_to_create.append(
                        PortfolioHolding(
                            portfolio_id=portfolio_id,
                            instrument_name=h.issuer_name[:255],
                            instrument_type="mutual_fund",
                            ticker_symbol=(h.isin or "")[:20] or None,
                            quantity=h.units,
                            average_cost=None,
                            current_price=h.last_traded_price,
                            current_value=current_val,
                            allocation_percentage=None,
                            exchange=None,
                            expense_ratio=None,
                            return_1y=None,
                            return_3y=None,
                            return_5y=None,
                        )
                    )
                else:
                    bucket_amounts["Equity"] += current_val
                    holdings_to_create.append(
                        PortfolioHolding(
                            portfolio_id=portfolio_id,
                            instrument_name=h.issuer_name[:255],
                            instrument_type="equity",
                            ticker_symbol=(h.isin or "")[:20] or None,
                            quantity=h.units,
                            average_cost=None,
                            current_price=h.last_traded_price,
                            current_value=current_val,
                            allocation_percentage=None,
                            exchange=None,
                            expense_ratio=None,
                            return_1y=None,
                            return_3y=None,
                            return_5y=None,
                        )
                    )

            if eq_current_value > eq_holdings_total:
                bucket_amounts["Equity"] += eq_current_value - eq_holdings_total

            linked = LinkedAccount(
                user_id=user.id,
                account_type=LinkedAccountType.stock_demat,
                provider_name=fip_id,
                account_identifier=account_ref_no,
                encrypted_access_token=None,
                status=LinkedAccountStatus.active,
                metadata_json={
                    "fi_type": fi_type,
                    "account_type": account_type,
                    "portfolio_bucket": "Equity",
                    "masked_demat_id": eq_summary.get("masked_demat_id"),
                    "current_value": eq_current_value,
                },
                linked_at=datetime.now(timezone.utc),
                last_synced_at=datetime.now(timezone.utc),
            )
            db.add(linked)
            await db.flush()
            linked_account_ids.append(linked.id)

    # Insert/update MF fund metadata
    if mf_fund_metadata_to_upsert:
        scheme_codes = list(mf_fund_metadata_to_upsert.keys())
        existing_rows = (await db.execute(select(MfFundMetadata).where(MfFundMetadata.scheme_code.in_(scheme_codes)))).scalars().all()
        existing_by_code = {r.scheme_code: r for r in existing_rows}

        for code, meta in mf_fund_metadata_to_upsert.items():
            if code in existing_by_code:
                row = existing_by_code[code]
                row.scheme_name = meta["scheme_name"]
                row.amc_name = meta["amc_name"]
                row.category = meta["category"]
                row.sub_category = meta["sub_category"]
                row.plan_type = meta["plan_type"]
                row.option_type = meta["option_type"]
            else:
                db.add(
                    MfFundMetadata(
                        scheme_code=meta["scheme_code"],
                        scheme_name=meta["scheme_name"],
                        amc_name=meta["amc_name"],
                        category=meta["category"],
                        sub_category=meta["sub_category"],
                        plan_type=meta["plan_type"],
                        option_type=meta["option_type"],
                        # Other fields are nullable/optional in schema; keep defaults.
                        is_active=True,
                    )
                )

    # Allocation rows: only canonical category buckets
    if total_value > 0:
        for bucket in ("Cash", "Debt", "Equity", "Other"):
            amount = bucket_amounts[bucket]
            if amount <= 0:
                continue
            pct = round((amount / total_value) * 100, 2)
            allocations_to_create.append(
                PortfolioAllocation(
                    portfolio_id=portfolio_id,
                    asset_class=bucket,
                    allocation_percentage=pct,
                    amount=amount,
                    performance_percentage=None,
                )
            )

    # Persist portfolio + ledger
    for h in holdings_to_create:
        db.add(h)
    for a in allocations_to_create:
        db.add(a)
    for t in mf_txns_to_create:
        db.add(t)

    portfolio.total_value = total_value
    portfolio.total_invested = total_invested
    portfolio.total_gain_percentage = (
        round(((total_value - total_invested) / total_invested) * 100, 2)
        if total_invested > 0
        else None
    )

    today = datetime.now(timezone.utc).date()
    db.add(PortfolioHistory(portfolio_id=portfolio_id, recorded_date=today, total_value=total_value))

    await db.commit()
    # Refresh portfolio object
    await db.refresh(portfolio)

    return portfolio, linked_account_ids

