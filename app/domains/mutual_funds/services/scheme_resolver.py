"""Resolve mutual-fund scheme identifiers to the canonical AMFI scheme code.

CAMS / KFintech CAS (and AA-feed) statements sometimes carry only an **ISIN** — or an
RTA-internal scheme code — instead of the AMFI scheme code. Both ``mf_nav_history`` and
the mfapi.in feed are keyed by the **numeric AMFI scheme code**, so an ISIN-keyed holding
or transaction can never be priced or refreshed (its NAV lookup misses, and a
fetch-by-ISIN against mfapi.in fails). To keep one identifier space across the app we map
ISIN → AMFI via ``mf_fund_metadata`` (the canonical, universe-sourced table) at ingest.

Resolution is best-effort: an ISIN we don't yet know (or a bare RTA code) is left as-is so
nothing is lost — run the mfapi universe ingest to widen ``mf_fund_metadata`` coverage.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models import MfFundMetadata


def is_amfi_code(code: str | None) -> bool:
    """True for an AMFI scheme code, which is purely numeric (e.g. ``"152712"``).

    ISINs (``INF…``) and RTA codes (``GFD``, ``44T``) are alphanumeric, so this cleanly
    distinguishes a canonical code from one that still needs resolving.
    """
    return bool(code) and str(code).strip().isdigit()


async def build_isin_to_amfi_map(db: AsyncSession, isins: set[str]) -> dict[str, str]:
    """Map upper-cased ISIN → canonical (numeric) AMFI ``scheme_code`` via ``mf_fund_metadata``.

    Matches both the growth ISIN (``isin``) and the dividend-reinvest ISIN
    (``isin_div_reinvest``). Only numeric scheme codes are returned — an ISIN-keyed
    metadata row is never itself a resolution target.
    """
    keys = {i.strip().upper() for i in isins if i and i.strip()}
    if not keys:
        return {}
    rows = (
        await db.execute(
            select(
                MfFundMetadata.scheme_code,
                MfFundMetadata.isin,
                MfFundMetadata.isin_div_reinvest,
            ).where(
                or_(
                    func.upper(MfFundMetadata.isin).in_(keys),
                    func.upper(MfFundMetadata.isin_div_reinvest).in_(keys),
                )
            )
        )
    ).all()
    out: dict[str, str] = {}
    for scheme_code, isin, isin_dr in rows:
        if not is_amfi_code(scheme_code):
            continue
        for cand in (isin, isin_dr):
            if cand and cand.strip().upper() in keys:
                out.setdefault(cand.strip().upper(), str(scheme_code).strip())
    return out


def canonical_scheme_code(
    *, amfi: str | None, isin: str | None, isin_to_amfi: dict[str, str]
) -> str | None:
    """Best canonical identifier for one scheme.

    Priority: an explicit numeric AMFI code → an ISIN resolved to AMFI → the raw value we
    were given (so an unknown ISIN / RTA code is preserved, not dropped). Returns ``None``
    only when neither an AMFI code nor an ISIN is present.
    """
    if is_amfi_code(amfi):
        return str(amfi).strip()
    if isin:
        hit = isin_to_amfi.get(isin.strip().upper())
        if hit:
            return hit
    # The ``amfi`` slot itself may actually hold an ISIN (CAS ingest stores amfi-or-isin).
    if amfi:
        hit = isin_to_amfi.get(str(amfi).strip().upper())
        if hit:
            return hit
    return (str(amfi).strip() if amfi else None) or (isin.strip() if isin else None)
