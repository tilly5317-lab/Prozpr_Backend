"""Materialise a RebalancingComputeRequest from User + GoalAllocationOutput + DB."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.profile.tax_profile import TaxProfile
from app.models.user import User
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.ai_bridge.rebalancing.fund_rank import FundRankRow, get_fund_ranking
from app.services.ai_bridge.rebalancing.holdings_ledger import (
    HoldingLedgerEntry,
    build_holdings_ledger,
)
from app.services.ai_bridge.rebalancing.tax_aging import (
    LotSplit,
    classify_lots_st_lt,
    count_units_in_exit_load_window,
)

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    GoalAllocationOutput,
)
from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowInput,
    RebalancingComputeRequest,
)


_DEFAULT_TAX_REGIME = "new"
_DEFAULT_TAX_RATE_PCT = 30.0
_DEFAULT_FUND_RATING = 10
_ROUNDING_STEP = 100


class _Unpriceable(Exception):
    """Raised when a recommended ISIN has no NAV available."""


async def _latest_nav_by_isin(
    db: AsyncSession, isins: set[str],
) -> dict[str, Decimal]:
    if not isins:
        return {}
    rows = (await db.execute(
        select(MfNavHistory.isin, MfNavHistory.nav, MfNavHistory.nav_date)
        .where(MfNavHistory.isin.in_(isins))
        .order_by(MfNavHistory.isin, MfNavHistory.nav_date.desc())
    )).all()
    out: dict[str, Decimal] = {}
    for isin, nav, _date in rows:
        out.setdefault(isin, Decimal(str(nav)))
    return out


async def _metadata_by_isin(
    db: AsyncSession, isins: set[str],
) -> dict[str, MfFundMetadata]:
    if not isins:
        return {}
    rows = (await db.execute(
        select(MfFundMetadata, MfNavHistory.isin)
        .join(MfNavHistory, MfNavHistory.scheme_code == MfFundMetadata.scheme_code)
        .where(MfNavHistory.isin.in_(isins))
        .distinct()
    )).all()
    return {isin: meta for meta, isin in rows}


def _resolve_tax_inputs(tax_profile: Optional[TaxProfile]) -> dict[str, Any]:
    if tax_profile is None:
        return {
            "tax_regime": _DEFAULT_TAX_REGIME,
            "effective_tax_rate_pct": _DEFAULT_TAX_RATE_PCT,
            "carryforward_st_loss_inr": Decimal(0),
            "carryforward_lt_loss_inr": Decimal(0),
        }
    return {
        "tax_regime": tax_profile.tax_regime or _DEFAULT_TAX_REGIME,
        "effective_tax_rate_pct": float(
            tax_profile.income_tax_rate or _DEFAULT_TAX_RATE_PCT
        ),
        "carryforward_st_loss_inr": Decimal(
            str(tax_profile.carryforward_st_loss_inr or 0)
        ),
        "carryforward_lt_loss_inr": Decimal(
            str(tax_profile.carryforward_lt_loss_inr or 0)
        ),
    }


def _build_row(
    *,
    rank_row: Optional[FundRankRow],
    held_entry: Optional[HoldingLedgerEntry],
    target_amount_pre_cap: Decimal,
    current_nav: Decimal,
    asset_class: str,
    exit_load_pct: float,
    exit_load_months: int,
    is_recommended: bool,
    fund_rating: int,
    asof: date,
    bad_subgroup: Optional[str] = None,
    bad_sub_category: Optional[str] = None,
    bad_fund_name: Optional[str] = None,
    bad_isin: Optional[str] = None,
) -> FundRowInput:
    if rank_row is not None:
        subgroup = rank_row.asset_subgroup
        sub_category = rank_row.sub_category
        fund_name = rank_row.fund_name
        isin = rank_row.isin
        rank = rank_row.rank
    else:
        subgroup = bad_subgroup or "unknown"
        sub_category = bad_sub_category or "unknown"
        fund_name = bad_fund_name or "unknown"
        isin = bad_isin or ""
        rank = 0

    if held_entry is not None:
        split: LotSplit = classify_lots_st_lt(
            held_entry.lots,
            asset_class=asset_class,
            current_nav=current_nav,
            as_of=asof,
        )
        units_in_load = count_units_in_exit_load_window(
            held_entry.lots,
            exit_load_months=exit_load_months,
            as_of=asof,
        )
        present = split.st_value_inr + split.lt_value_inr
        invested = split.st_cost_inr + split.lt_cost_inr
    else:
        split = LotSplit(Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        units_in_load = Decimal(0)
        present = Decimal(0)
        invested = Decimal(0)

    return FundRowInput(
        asset_subgroup=subgroup,
        sub_category=sub_category,
        recommended_fund=fund_name,
        isin=isin,
        rank=rank,
        target_amount_pre_cap=target_amount_pre_cap,
        present_allocation_inr=present,
        invested_cost_inr=invested,
        st_value_inr=split.st_value_inr,
        st_cost_inr=split.st_cost_inr,
        lt_value_inr=split.lt_value_inr,
        lt_cost_inr=split.lt_cost_inr,
        exit_load_pct=exit_load_pct,
        exit_load_months=exit_load_months,
        units_within_exit_load_period=units_in_load,
        current_nav=current_nav,
        fund_rating=fund_rating,
        is_recommended=is_recommended,
    )


async def build_rebalancing_input_for_user(
    user: User,
    allocation_output: GoalAllocationOutput,
    db: AsyncSession,
) -> tuple[RebalancingComputeRequest, dict[str, Any]]:
    """Return ``(request, debug_dict)`` for ``run_rebalancing(...)``."""
    asof = date.today()

    # 1. Holdings ledger.
    ledger = await build_holdings_ledger(db, user_id=user.id)
    held_by_isin: dict[str, HoldingLedgerEntry] = {e.isin: e for e in ledger}

    # 2. Sub-asset-group targets from allocation.
    target_by_subgroup: dict[str, Decimal] = {}
    for r in allocation_output.aggregated_subgroups:
        target_by_subgroup[r.subgroup] = Decimal(str(r.total))

    # 3. Fund-rank table.
    ranking = get_fund_ranking()
    recommended_isins: set[str] = {
        rr.isin for rows in ranking.values() for rr in rows
    }

    # 4. Bulk-fetch NAV + metadata for everything we need.
    held_isins = set(held_by_isin)
    all_isins = recommended_isins | held_isins
    nav_by_isin = await _latest_nav_by_isin(db, all_isins)
    meta_by_isin = await _metadata_by_isin(db, all_isins)

    rows: list[FundRowInput] = []
    seen_isins: set[str] = set()

    # 5. Recommended-fund rows.
    for subgroup, rank_rows in ranking.items():
        rank1_target = target_by_subgroup.get(subgroup, Decimal(0))
        for rr in rank_rows:
            held = held_by_isin.get(rr.isin)
            current_nav = nav_by_isin.get(rr.isin)
            if current_nav is None:
                if held is None:
                    raise _Unpriceable(
                        f"recommended ISIN {rr.isin} ({rr.fund_name}) has no NAV"
                    )
                # Fallback for held ISIN: latest acquisition_nav as conservative price.
                current_nav = held.lots[-1].acquisition_nav

            meta = meta_by_isin.get(rr.isin)
            asset_class = (meta.asset_class if meta else None) or "equity"
            exit_load_pct = float(meta.exit_load_percent or 0.0) if meta else 0.0
            exit_load_months = int(meta.exit_load_months or 0) if meta else 0

            rows.append(_build_row(
                rank_row=rr,
                held_entry=held,
                target_amount_pre_cap=rank1_target if rr.rank == 1 else Decimal(0),
                current_nav=current_nav,
                asset_class=asset_class,
                exit_load_pct=exit_load_pct,
                exit_load_months=exit_load_months,
                is_recommended=True,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
            ))
            seen_isins.add(rr.isin)

    # 6. BAD-fund rows.
    bad_count = 0
    for isin, entry in held_by_isin.items():
        if isin in seen_isins:
            continue
        meta = meta_by_isin.get(isin)
        current_nav = nav_by_isin.get(isin) or entry.lots[-1].acquisition_nav
        asset_class = (meta.asset_class if meta else None) or "equity"
        rows.append(_build_row(
            rank_row=None,
            held_entry=entry,
            target_amount_pre_cap=Decimal(0),
            current_nav=current_nav,
            asset_class=asset_class,
            exit_load_pct=float(meta.exit_load_percent or 0.0) if meta else 0.0,
            exit_load_months=int(meta.exit_load_months or 0) if meta else 0,
            is_recommended=False,
            fund_rating=_DEFAULT_FUND_RATING,
            asof=asof,
            bad_subgroup=(meta.asset_subgroup if meta else "unknown"),
            bad_sub_category=(meta.sub_category if meta else "unknown"),
            bad_fund_name=(meta.scheme_name if meta else entry.scheme_code),
            bad_isin=isin,
        ))
        bad_count += 1

    # 7. Total corpus = sum of held market values.
    total_corpus = sum(
        (r.present_allocation_inr for r in rows if r.present_allocation_inr > 0),
        start=Decimal(0),
    )

    # 8. Tax inputs. Query directly — relationship may not be eager-loaded.
    tax_profile = (await db.execute(
        select(TaxProfile).where(TaxProfile.user_id == user.id)
    )).scalar_one_or_none()
    tax_inputs = _resolve_tax_inputs(tax_profile)

    request = RebalancingComputeRequest(
        total_corpus=total_corpus,
        tax_regime=tax_inputs["tax_regime"],
        effective_tax_rate_pct=tax_inputs["effective_tax_rate_pct"],
        rounding_step=_ROUNDING_STEP,
        stcg_offset_budget_inr=None,
        carryforward_st_loss_inr=tax_inputs["carryforward_st_loss_inr"],
        carryforward_lt_loss_inr=tax_inputs["carryforward_lt_loss_inr"],
        rows=rows,
    )
    debug = {
        "total_corpus": str(total_corpus),
        "lots_per_isin": {e.isin: len(e.lots) for e in ledger},
        "bad_fund_count": bad_count,
        "row_count": len(rows),
    }
    return request, debug
