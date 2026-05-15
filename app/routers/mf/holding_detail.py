"""HTTP route — MF holding detail page.

``GET /api/v1/mf/funds/{scheme_code}/holding-detail`` returns, in one call, the
data a fund-detail screen needs:

* scheme facts (name, AMC, category, ISIN, plan/option type, ``metadata_id``),
* the NAV time series for the chart (``mf_nav_history``; default last ~5 years,
  override with ``date_from`` / ``date_to``),
* the signed-in user's current position in the scheme (units, average cost,
  value, weight, unrealised gain — summed across folios), and
* the user's transaction ledger in that scheme, each row flagged ``is_inflow``
  (BUY / SWITCH_IN / DIVIDEND_REINVEST) so the UI can colour inflows green and
  redemptions / switch-outs red, plus a ready-to-render ``signed_amount``.

``scheme_code`` may be an AMFI scheme code **or** an ISIN — portfolio holdings
from the CAMS CAS ingest store whichever the statement carried.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import MfHoldingDetailResponse
from app.services.mf import mf_holding_detail_service

router = APIRouter(prefix="/funds", tags=["MF Data"])


@router.get(
    "/{scheme_code}/holding-detail",
    response_model=MfHoldingDetailResponse,
    summary="Fund detail + the user's position and transaction ledger in it",
)
async def get_holding_detail(
    scheme_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    date_from: Optional[date] = Query(
        None, description="Start of the NAV series (default: ~5 years ago)."
    ),
    date_to: Optional[date] = Query(
        None, description="End of the NAV series (default: today)."
    ),
):
    return await mf_holding_detail_service.build_holding_detail(
        db, current_user.id, scheme_code, date_from=date_from, date_to=date_to
    )
