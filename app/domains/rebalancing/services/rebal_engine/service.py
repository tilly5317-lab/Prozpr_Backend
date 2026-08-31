"""Cache-first rebalancing orchestrator.

Reads the most recent goal allocation for the user; if it's > 90 days old or
absent, re-runs allocation inline. Then materialises engine inputs, runs the
pipeline on a worker thread, persists the trade-list, and renders chat markdown.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

from app.domains.asset_allocation.models.run import AssetAllocationRun
from app.domains.mutual_funds.models.mf_allocation_snapshot import (
    PortfolioAllocationSnapshot,
)
from app.domains.mutual_funds.models.enums import PortfolioSnapshotKind
from app.domains.asset_allocation.services.aa_engine.service import (
    AllocationRunOutcome,
    compute_allocation_result,
)
from app.domains.ai_engine.common import (
    ensure_ai_agents_path,
    format_inr_indian,
    trace_line,
)
from app.domains.rebalancing.services.rebal_engine.formatter import (
    build_fallback_rebal_brief,
)
from app.domains.rebalancing.services.rebal_engine.input_builder import (
    build_rebalancing_input_for_user,
)
from app.domains.chat.services.ai_module_telemetry import record_ai_module_run
from app.domains.portfolio.services.portfolio_service import (
    get_or_create_primary_portfolio,
)
from app.domains.rebalancing.services.rebalancing_persist_service import (
    persist_rebalancing_recommendation,
)

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    GoalAllocationOutput,
)
from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    RebalancingComputeResponse,
)
from Rebalancing.pipeline import run_rebalancing  # type: ignore[import-not-found]  # noqa: E402


logger = logging.getLogger(__name__)

ALLOCATION_TTL_DAYS = 90


_MSG_MISSING_DOB = (
    "I need your date of birth to plan trades — it anchors your tax aging "
    "and risk profile. Add it on your profile and ask me again."
)
_MSG_NO_HOLDINGS = "Connect your mutual fund portfolio and ask me again."
_MSG_ENGINE_ERROR = (
    "I couldn't compute your rebalancing plan right now. Try again in a moment, "
    "and if it keeps happening let us know via the help option."
)
_MSG_UNPRICEABLE = (
    "I couldn't price one of the recommended funds — looks like our market data "
    "is missing for it. Try again later or let us know via help."
)

# Data-gap gates the customer can resolve (missing DOB, no holdings) are tailored
# through the formatter; transient/data-quality error blockers stay verbatim.
TAILORABLE_BLOCKERS: frozenset[str] = frozenset({_MSG_MISSING_DOB, _MSG_NO_HOLDINGS})


@dataclass(frozen=True)
class RebalancingRunOutcome:
    response: Optional[RebalancingComputeResponse]
    formatted_text: Optional[str] = None
    blocking_message: Optional[str] = None
    recommendation_id: Optional[uuid.UUID] = None
    allocation_snapshot_id: Optional[uuid.UUID] = None
    source_allocation_id: Optional[uuid.UUID] = None
    used_cached_allocation: bool = False
    # Goal-tied bucket block derived from the AA output that drove this rebalance.
    # None when AA output wasn't available; consumed by the formatter facts pack.
    goal_buckets: Optional[list[dict[str, Any]]] = None


FUND_ACTIONS_LIMIT = 30


_BUCKET_HORIZON_LABELS = {
    "emergency": "Emergency reserve",
    "short_term": "Short-term (< 3 yrs)",
    "medium_term": "Medium-term (3-5 yrs)",
    "long_term": "Long-term (> 5 yrs)",
}


def build_goal_buckets_block(
    allocation_output: "GoalAllocationOutput",
) -> list[dict[str, Any]]:
    """Goal-tied bucket view derived from the AA output that drove this rebalance.

    Lets the formatter LLM tie trades back to the goals and equity/debt/others
    split that justified them. One entry per bucket the customer has goals in,
    plus the planned asset-class % split for that bucket.

    Shape (one entry per bucket):
      {
        "bucket": "long_term",
        "horizon_label": "Long-term (> 5 yrs)",
        "goals": [{
          "name": <str>,
          "horizon_months": <int>,
          "amount_needed_inr": <float>, "amount_needed_indian": <str>,
          "priority": "non_negotiable" | "negotiable",
        }, ...],
        "total_goal_amount_inr": <float>, "total_goal_amount_indian": <str>,
        "allocated_amount_inr":  <float>, "allocated_amount_indian":  <str>,
        "planned_split_pct": {"equity": <float>, "debt": <float>, "others": <float>},
      }
    """
    per_bucket_split = {
        bs.bucket: bs
        for bs in allocation_output.asset_class_breakdown.planned.per_bucket
    }
    out: list[dict[str, Any]] = []
    for bucket_alloc in allocation_output.bucket_allocations:
        if not bucket_alloc.goals and bucket_alloc.bucket != "emergency":
            # Skip empty non-emergency buckets — nothing meaningful to anchor.
            continue
        split = per_bucket_split.get(bucket_alloc.bucket)
        out.append(
            {
                "bucket": bucket_alloc.bucket,
                "horizon_label": _BUCKET_HORIZON_LABELS.get(
                    bucket_alloc.bucket,
                    bucket_alloc.bucket,
                ),
                "goals": [
                    {
                        "name": g.goal_name,
                        "horizon_months": g.time_to_goal_months,
                        "amount_needed_inr": float(g.amount_needed),
                        "amount_needed_indian": format_inr_indian(g.amount_needed),
                        "priority": g.goal_priority,
                    }
                    for g in bucket_alloc.goals
                ],
                "total_goal_amount_inr": float(bucket_alloc.total_goal_amount),
                "total_goal_amount_indian": format_inr_indian(
                    bucket_alloc.total_goal_amount,
                ),
                "allocated_amount_inr": float(bucket_alloc.allocated_amount),
                "allocated_amount_indian": format_inr_indian(
                    bucket_alloc.allocated_amount,
                ),
                "planned_split_pct": {
                    "equity": round(float(split.equity_pct)) if split else 0,
                    "debt": round(float(split.debt_pct)) if split else 0,
                    "others": round(float(split.others_pct)) if split else 0,
                },
            }
        )
    return out


def ideal_asset_class_mix_pct(response: Any) -> Optional[dict[str, float]]:
    """The goals-and-risk IDEAL Equity/Debt/Others split, as raw percentages.

    This is what the customer SHOULD hold on their goals and risk profile alone,
    before any holdings reality. It is NOT the rebalancing target — the target is
    what this plan can actually reach given current holdings, lock-ins and the
    trades it is willing to make, and the two legitimately differ (72/21/7 ideal
    vs 83/13/4 target on a real portfolio). Customers compare the two and ask why,
    so both belong in the facts pack; with only one of them present the answer
    gets improvised.

    Returns None when the response carries no practical allocation.
    """
    planned = getattr(
        getattr(
            getattr(response, "practical_allocation", None),
            "asset_class_breakdown",
            None,
        ),
        "planned",
        None,
    )
    if planned is None:
        return None
    return {
        "equity": float(getattr(planned, "equity_total_pct", 0.0) or 0.0),
        "debt": float(getattr(planned, "debt_total_pct", 0.0) or 0.0),
        "others": float(getattr(planned, "others_total_pct", 0.0) or 0.0),
    }


def _asset_class_mix_from_buckets(
    buckets: list[dict[str, Any]],
    *,
    amount_key: str,
    multi_asset_sleeve: bool,
) -> dict[str, float]:
    """Lowercase Equity/Debt/Others ₹ mix from fact-pack buckets.

    Delegates to the SHARED rollup that also builds the Invest-page bars, so chat
    and the page cannot disagree about one run. ``amount_key`` picks the column
    (``current_inr`` for the current mix, ``planned_final_inr`` for the target);
    ``multi_asset_sleeve`` must be True only for the target — see
    ``asset_class_breakdown`` for why. Built in canonical title-case, then mapped
    to this builder's long-standing lowercase contract for chat facts.
    """
    from app.domains.rebalancing.services.asset_class_breakdown import (
        asset_class_mix_from_rows,
    )

    title_mix = asset_class_mix_from_rows(
        (
            (
                bucket.get("asset_subgroup"),
                bucket.get("sub_category"),
                float(bucket.get(amount_key, 0.0) or 0.0),
            )
            for bucket in buckets
        ),
        multi_asset_sleeve=multi_asset_sleeve,
    )
    return {
        "equity": title_mix.get("Equity", 0.0),
        "debt": title_mix.get("Debt", 0.0),
        "others": title_mix.get("Others", 0.0),
    }


# asset_subgroup -> customer-facing group label for the group_flows subtotals.
# Covers every subgroup in SUBGROUP_TO_ASSET_CLASS (+ ELSS, near_debt); anything
# unmapped falls back to "Other funds" so a raw internal subgroup name never leaks
# to the customer (the formatter is forbidden from surfacing asset_subgroup).
_SUBGROUP_FLOW_LABEL: dict[str, str] = {
    "low_beta_equities": "Large-cap equity",
    "medium_beta_equities": "Mid-cap equity",
    "high_beta_equities": "Small-cap equity",
    "value_equities": "Value & contra equity",
    "dividend_equities": "Dividend equity",
    "sector_equities": "Sectoral & thematic equity",
    "us_equities": "US & international equity",
    "multi_asset": "Multi-asset & hybrid funds",
    "tax_efficient_equities": "ELSS (tax-saving)",
    "short_debt": "Debt funds",
    "near_debt": "Debt funds",
    "arbitrage": "Arbitrage & income funds",
    "arbitrage_plus_income": "Arbitrage & income funds",
    "gold_commodities": "Gold & commodities",
    "silver_commodities": "Gold & commodities",
    "china_equities": "China & EM equity",
    "others_fofs": "Other funds",
    "others": "Other funds",
}

# Keys stripped from the OUTPUT list rows. The *_inr floats are redundant with the
# pre-formatted *_indian strings the formatter cites (it never does math), and
# asset_subgroup is internal — the prompt already forbids surfacing it. Both are
# still read DURING construction (asset-class rollup, group_flows, sorting); they
# are removed only from the final rows to keep the pack lean.
_ROW_DROP = ("current_inr", "buy_inr", "sell_inr", "planned_final_inr", "asset_subgroup")
_GROUP_FLOW_DROP = ("current_inr", "buy_inr", "sell_inr", "planned_final_inr", "net_change_inr")


def _slim_row(row: dict[str, Any], drop: tuple[str, ...]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in drop}


def _signed_indian(value: float) -> str:
    """Pre-formatted net change with an explicit sign (matches change_indian)."""
    if value > 0:
        return "+" + format_inr_indian(value)
    if value < 0:
        return "−" + format_inr_indian(abs(value))
    return format_inr_indian(0)


def build_rebal_facts_pack(
    response: "RebalancingComputeResponse",
    *,
    goal_buckets: Optional[list[dict[str, Any]]] = None,
    constraint_impact: Optional[dict[str, Any]] = None,
    is_rerun: bool = False,
    fund_house_view: Optional[str] = None,
    include_ideal: bool = True,
) -> dict[str, Any]:
    """Curated facts the LLM may cite. Customer-tellable only — no ISIN.

    Shape:
      {
        "total_portfolio_inr": <float>, "total_portfolio_indian": <str>,
        "buys_total_inr":      <float>, "buys_total_indian":      <str>,
        "sells_total_inr":     <float>, "sells_total_indian":     <str>,
        # Present only when the NFA band trims direct equity. These rupees
        # fund MF buys but are NOT inside sells_total, so buys_total can
        # legitimately exceed sells_total by this amount.
        "direct_stock_sale_inr":   <float>,
        "direct_stock_sale_indian": <str>,
        "tax_impact_inr":      <float>, "tax_impact_indian":      <str>,

        # How the tax bill above splits by holding period. A low/zero
        # stcg_realised is the evidence the plan is already tax-optimised: trims
        # sell long-term units first and leave short-term units untouched (STCG
        # only ever comes from a forced exit). stcg_offset_by_losses is STCG
        # cancelled by short-term losses.
        "tax_treatment": {
            "ltcg_realised_inr":            <float>, "ltcg_realised_indian":            <str>,
            "stcg_realised_inr":            <float>, "stcg_realised_indian":            <str>,
            "stcg_offset_by_losses_inr":    <float>, "stcg_offset_by_losses_indian":    <str>,
        },
        "trade_count":         int,

        # Tax rules actually used by the engine for the LTCG / STCG figures
        # above. Surfaced so the formatter cites real rates rather than
        # priors from training data (pre-2024 LTCG was 10% / ₹1 lakh, now
        # 12.5% / ₹1.25 lakh). Omitted only if metadata is unavailable.
        "tax_rules": {
            "ltcg_rate_equity_pct":          <float>,   # e.g. 12.5
            "stcg_rate_equity_pct":          <float>,   # e.g. 20.0
            "ltcg_annual_exemption_inr":     <float>,
            "ltcg_annual_exemption_indian":  <str>,     # e.g. "₹1.25 lakh"
            "equity_long_term_threshold_months": <int>, # e.g. 12
        },

        # High-level asset-class summary. CURRENT is what the customer holds
        # today; TARGET is the post-trade mix the plan moves them to. Both come
        # from the shared rollup behind the Invest-page bars, so the two surfaces
        # always agree. Ship BOTH — with only the current mix present, the
        # formatter answered "what is the plan moving me toward?" by citing it.
        "current_asset_class_mix_pct":    {"equity": <float>, "debt": <float>, "others": <float>},
        "current_asset_class_mix_indian": {"equity": <str>,   "debt": <str>,   "others": <str>},
        "target_asset_class_mix_pct":     {"equity": <float>, "debt": <float>, "others": <float>},
        "target_asset_class_mix_indian":  {"equity": <str>,   "debt": <str>,   "others": <str>},

        # One bucket per sub_category — the SEBI label (e.g., "Large Cap Fund"),
        # the customer-facing name. Output rows carry only the pre-formatted
        # *_indian amounts; the *_inr floats and the internal asset_subgroup are
        # dropped from the row (both are used only DURING pack construction).
        "buckets": [{
            "sub_category": <str>,                                       # e.g. "Large Cap Fund"
            "current_indian": <str>, "buy_indian": <str>,
            "sell_indian":    <str>, "planned_final_indian": <str>,
        }, ...],

        # Group-level rollup, one per customer-facing group label (buckets rolled up
        # by asset_subgroup), largest holding first. It is BOTH the customer-facing
        # Current->Buy->Sell->Planned table (a ~9-row replacement for the ~16-row
        # per-SEBI-category table) AND the pre-computed subtotal the formatter cites
        # verbatim for "where the money goes" (so it never sums buckets in prose —
        # that arithmetic hallucinated crore figures). Each group carries its own
        # held total, so "sell X out of Y held" pairs correctly at the group level.
        "group_flows": [{
            "group": <str>,                                       # e.g. "Multi-asset & hybrid funds"
            "current_indian": <str>,
            "buy_indian": <str>, "sell_indian": <str>,
            "net_change_indian": <str>,                           # signed, "+₹2.42 crore" / "−₹1.75 crore"
            "planned_final_indian": <str>,
        }, ...],

        "warnings": [<short_string>, ...],   # human-readable, <= 5 entries

        # Per-fund actions: every fund with a trade first (so none is ever cut),
        # then held-as-is rows by exposure, capped at FUND_ACTIONS_LIMIT. Lets the
        # LLM narrate fund-specific questions ("why are you trimming HDFC Top 100?").
        # No ISINs — fund_name is customer-tellable, ISIN isn't. Output rows carry
        # only *_indian amounts (the *_inr floats and internal asset_subgroup are
        # dropped from the row; both used only during construction).
        "fund_actions": [{
            "fund_name":      <str>,                                         # e.g. "HDFC Top 100"
            "sub_category":   <str>,                                         # SEBI category
            "current_indian": <str>, "buy_indian": <str>,
            "sell_indian":    <str>, "planned_final_indian": <str>,
            "reason":         <str>,                                         # selection_reason for buys, joined rejection reasons for sells; "" otherwise
        }, ...],
        # Number of additional smaller holdings beyond fund_actions cap
        # (only present when truncated).
        "more_holdings_count": <int>,

        # Optional — present when AA output drove this rebalance. Lets the LLM
        # tie trades back to goals + horizon + planned equity/debt/others split.
        # See ``build_goal_buckets_block`` for shape.
        "goal_buckets": [...],

        # Present only on an explicit re-run ("rebalance again"). The formatter
        # leads with what changed instead of introducing the plan.
        "is_rerun": True,
      }

    Money convention: every numeric ``*_inr`` field is paired with a sibling
    ``*_indian`` string pre-formatted in Indian notation. The chat formatter
    prompt instructs the LLM to copy ``*_indian`` verbatim and never compute
    its own lakh/crore conversion.

    Fields are derived from ``response``; absent fields become 0/empty list.
    """
    rows = list(getattr(response, "rows", []) or [])
    warnings_list = list(getattr(response, "warnings", []) or [])

    buys_total = sum(float(getattr(r, "pass1_buy_amount", 0) or 0) for r in rows)
    sells_total = sum(float(getattr(r, "pass1_sell_amount", 0) or 0) for r in rows)

    # totals is a RebalancingTotals object; fall back to computed if absent
    totals_obj = getattr(response, "totals", None)
    tax_impact = float(getattr(totals_obj, "total_tax_estimate_inr", 0) or 0)
    # Tax-lot outcome: how the tax bill splits long- vs short-term, and STCG
    # offset by losses. A low/zero stcg_realised is the concrete evidence of the
    # engine's regulatory rule — trims sell long-term units first and leave
    # short-term units untouched (STCG only on a forced exit). Without this the
    # narrator has the total bill but can't say the plan is already tax-optimised.
    ltcg_realised = float(getattr(totals_obj, "total_ltcg_realised", 0) or 0)
    stcg_realised = float(getattr(totals_obj, "total_stcg_realised", 0) or 0)
    stcg_offset_by_losses = float(getattr(totals_obj, "total_stcg_net_off", 0) or 0)
    total_buy_inr = float(
        getattr(totals_obj, "total_buy_inr", buys_total) or buys_total
    )
    total_sell_inr = float(
        getattr(totals_obj, "total_sell_inr", sells_total) or sells_total
    )

    # Derive portfolio total from subgroup current holdings (not gross trade volume).
    subgroups = list(getattr(response, "subgroups", []) or [])
    total_portfolio = sum(
        float(getattr(sg, "current_holding_inr", 0) or 0) for sg in subgroups
    )

    # Aggregate per-fund actions into (asset_subgroup, sub_category) buckets,
    # and collect per-fund rows for fund_actions.
    # This mirrors formatter._bucketise so the LLM and the deterministic
    # fallback brief speak the same language. The customer-facing key is
    # ``sub_category`` (SEBI category like "Large Cap Fund") — never
    # ``asset_subgroup`` (internal engine grouping).
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    fund_rows: list[dict[str, Any]] = []
    for sg in subgroups:
        sg_subgroup = getattr(sg, "asset_subgroup", None)
        for action in getattr(sg, "actions", []) or []:
            present = float(getattr(action, "present_allocation_inr", 0) or 0)
            buy = float(getattr(action, "pass1_buy_amount", 0) or 0)
            sell = float(
                (getattr(action, "pass1_sell_amount", 0) or 0)
                + (getattr(action, "pass2_sell_amount", 0) or 0)
            )
            # Skip phantom rows (no holding, no buy, no sell).
            if present <= 0 and buy <= 0 and sell <= 0:
                continue
            sub_cat = getattr(action, "sub_category", None)
            key = (sg_subgroup, sub_cat)
            bucket = by_key.get(key)
            if bucket is None:
                bucket = {
                    "sub_category": sub_cat,
                    "asset_subgroup": sg_subgroup,
                    "current_inr": 0.0,
                    "buy_inr": 0.0,
                    "sell_inr": 0.0,
                }
                by_key[key] = bucket
            bucket["current_inr"] += present
            bucket["buy_inr"] += buy
            bucket["sell_inr"] += sell

            fund_name = getattr(action, "recommended_fund", None)
            if fund_name:
                # Per-fund rationale: selection text on a buy, rejection text on
                # a sell. None when neither (held-as-is row). Lets the LLM cite
                # "why this fund" on customer follow-up without computing it.
                if buy > 0:
                    reason = getattr(action, "selection_reason", None) or ""
                elif sell > 0:
                    reason = getattr(action, "rejection_reason", None) or ""
                else:
                    reason = ""
                fund_rows.append(
                    {
                        "fund_name": fund_name,
                        "sub_category": sub_cat,
                        "asset_subgroup": sg_subgroup,
                        "current_inr": present,
                        "buy_inr": buy,
                        "sell_inr": sell,
                        "planned_final_inr": present + buy - sell,
                        "reason": reason,
                    }
                )

    buckets: list[dict[str, Any]] = []
    for bucket in by_key.values():
        bucket["planned_final_inr"] = (
            bucket["current_inr"] + bucket["buy_inr"] - bucket["sell_inr"]
        )
        bucket["current_indian"] = format_inr_indian(bucket["current_inr"])
        bucket["buy_indian"] = format_inr_indian(bucket["buy_inr"])
        bucket["sell_indian"] = format_inr_indian(bucket["sell_inr"])
        bucket["planned_final_indian"] = format_inr_indian(bucket["planned_final_inr"])
        buckets.append(bucket)

    # Group-level buy/sell subtotals (one per customer-facing group label),
    # aggregated FROM `buckets` so a group total is exactly the sum of the rows the
    # LLM already sees. The formatter cites these verbatim for "where the money
    # goes" summaries instead of summing bucket amounts in prose — the LLM's mental
    # arithmetic on a multi-bucket group produced crore-scale hallucinations
    # ("multi-asset funds — ₹10.37 crore" against a ₹1.48 crore total buy).
    # current/planned_final carried too so the group is the customer-facing TABLE
    # (Current -> Buy -> Sell -> Planned, one row per group instead of ~16 SEBI
    # rows), and so the group holds its OWN held total — a "sell X out of Y held"
    # line then pairs the group sell with the GROUP's held, not a single category's.
    _z = lambda: {"current_inr": 0.0, "buy_inr": 0.0, "sell_inr": 0.0, "planned_final_inr": 0.0}  # noqa: E731
    group_acc: dict[str, dict[str, float]] = {}
    for bucket in buckets:
        label = _SUBGROUP_FLOW_LABEL.get(bucket["asset_subgroup"], "Other funds")
        g = group_acc.setdefault(label, _z())
        for k in ("current_inr", "buy_inr", "sell_inr", "planned_final_inr"):
            g[k] += bucket[k]
    group_flows = [
        {
            "group": label,
            "current_inr": v["current_inr"], "current_indian": format_inr_indian(v["current_inr"]),
            "buy_inr": v["buy_inr"], "buy_indian": format_inr_indian(v["buy_inr"]),
            "sell_inr": v["sell_inr"], "sell_indian": format_inr_indian(v["sell_inr"]),
            # net_change = buy - sell (= planned - current), pre-signed for the table's
            # middle column so the LLM never computes or signs it.
            "net_change_inr": v["buy_inr"] - v["sell_inr"],
            "net_change_indian": _signed_indian(v["buy_inr"] - v["sell_inr"]),
            "planned_final_inr": v["planned_final_inr"],
            "planned_final_indian": format_inr_indian(v["planned_final_inr"]),
        }
        for label, v in sorted(
            group_acc.items(),
            key=lambda kv: -max(kv[1]["current_inr"], kv[1]["planned_final_inr"]),
        )
        if v["current_inr"] > 0 or v["buy_inr"] > 0 or v["sell_inr"] > 0
    ]

    # Asset-class mix, CURRENT and TARGET. Both go through the shared rollup that
    # builds the Invest-page bars. The target is the post-trade mix (per-bucket
    # planned_final = current + buy - sell) and keeps the multi_asset sleeve at
    # its engine composition. Shipping only the current mix is what let the
    # formatter answer "what is the plan moving me toward?" with the current one.
    def _mix_block(amount_key: str, *, multi_asset_sleeve: bool):
        inr = _asset_class_mix_from_buckets(
            buckets, amount_key=amount_key, multi_asset_sleeve=multi_asset_sleeve
        )
        total = sum(inr.values()) or 0.0
        pct = {
            cls: (round(amt / total * 100) if total > 0 else 0)
            for cls, amt in inr.items()
        }
        return inr, pct, {cls: format_inr_indian(amt) for cls, amt in inr.items()}

    asset_class_inr, asset_class_pct, asset_class_indian = _mix_block(
        "current_inr", multi_asset_sleeve=False
    )
    target_class_inr, target_class_pct, target_class_indian = _mix_block(
        "planned_final_inr", multi_asset_sleeve=True
    )

    warnings: list[str] = []
    for w in warnings_list[:5]:
        msg = getattr(w, "message", None) or str(w)
        warnings.append(msg)

    # fund_actions: top FUND_ACTIONS_LIMIT by max(current, planned_final).
    # Aggregate any duplicate (fund_name, sub_category) rows from multiple
    # rank slots so the LLM sees one entry per actual fund.
    fund_by_name: dict[tuple[str, Any], dict[str, Any]] = {}
    for fr in fund_rows:
        key = (fr["fund_name"], fr["sub_category"])
        existing = fund_by_name.get(key)
        if existing is None:
            fund_by_name[key] = fr
        else:
            existing["current_inr"] += fr["current_inr"]
            existing["buy_inr"] += fr["buy_inr"]
            existing["sell_inr"] += fr["sell_inr"]
            existing["planned_final_inr"] += fr["planned_final_inr"]

    # Funds WITH a trade sort ahead of held-as-is rows (then by exposure), so the
    # cap can only ever drop zero-trade holdings — every buy/sell survives, which
    # lets the formatter show the full trade list on a small plan (< 10 trades).
    fund_actions_all = sorted(
        fund_by_name.values(),
        key=lambda f: (
            0 if (f["buy_inr"] > 0 or f["sell_inr"] > 0) else 1,
            -max(f["current_inr"], f["planned_final_inr"]),
        ),
    )
    fund_actions = fund_actions_all[:FUND_ACTIONS_LIMIT]
    for fa in fund_actions:
        fa["current_indian"] = format_inr_indian(fa["current_inr"])
        fa["buy_indian"] = format_inr_indian(fa["buy_inr"])
        fa["sell_indian"] = format_inr_indian(fa["sell_inr"])
        fa["planned_final_indian"] = format_inr_indian(fa["planned_final_inr"])
    more_holdings_count = max(0, len(fund_actions_all) - FUND_ACTIONS_LIMIT)

    # Surface the actual tax rates / exemption the engine used, so the formatter
    # can cite them instead of falling back on training-data priors (Haiku tends
    # to narrate the pre-July-2024 10% LTCG + ₹1 lakh exemption otherwise).
    knob = getattr(getattr(response, "metadata", None), "knob_snapshot", None)
    tax_rules: Optional[dict[str, Any]] = None
    if knob is not None:
        ltcg_exemption_inr = float(getattr(knob, "ltcg_annual_exemption_inr", 0) or 0)
        tax_rules = {
            "ltcg_rate_equity_pct": float(
                getattr(knob, "ltcg_rate_equity_pct", 0) or 0
            ),
            "stcg_rate_equity_pct": float(
                getattr(knob, "stcg_rate_equity_pct", 0) or 0
            ),
            "ltcg_annual_exemption_inr": ltcg_exemption_inr,
            "ltcg_annual_exemption_indian": format_inr_indian(ltcg_exemption_inr),
            "equity_long_term_threshold_months": int(
                getattr(knob, "st_threshold_months_equity", 0) or 0
            ),
        }

    pack: dict[str, Any] = {
        "total_portfolio_inr": total_portfolio,
        "total_portfolio_indian": format_inr_indian(total_portfolio),
        "buys_total_inr": total_buy_inr,
        "buys_total_indian": format_inr_indian(total_buy_inr),
        "sells_total_inr": total_sell_inr,
        "sells_total_indian": format_inr_indian(total_sell_inr),
        "tax_impact_inr": tax_impact,
        "tax_impact_indian": format_inr_indian(tax_impact),
        "tax_treatment": {
            "ltcg_realised_inr": ltcg_realised,
            "ltcg_realised_indian": format_inr_indian(ltcg_realised),
            "stcg_realised_inr": stcg_realised,
            "stcg_realised_indian": format_inr_indian(stcg_realised),
            "stcg_offset_by_losses_inr": stcg_offset_by_losses,
            "stcg_offset_by_losses_indian": format_inr_indian(stcg_offset_by_losses),
        },
        "trade_count": sum(
            1
            for r in rows
            if (
                float(getattr(r, "pass1_buy_amount", 0) or 0) > 0
                or float(getattr(r, "pass1_sell_amount", 0) or 0) > 0
            )
        ),
        "current_asset_class_mix_pct": asset_class_pct,
        "current_asset_class_mix_indian": asset_class_indian,
        "target_asset_class_mix_pct": target_class_pct,
        "target_asset_class_mix_indian": target_class_indian,
        "buckets": [_slim_row(b, _ROW_DROP) for b in buckets],
        "group_flows": [_slim_row(g, _GROUP_FLOW_DROP) for g in group_flows],
        "warnings": warnings,
        "fund_actions": [_slim_row(f, _ROW_DROP) for f in fund_actions],
    }
    # Direct-stock proceeds. Step4 funds MF buys with these
    # (`excess_direct_stocks_inr`), but they are NOT part of `total_sell_inr`,
    # which sums over fund rows only. Without this the pack shows buys far
    # exceeding sells with no way to account for the difference — Neha's plan
    # buys ₹16.5L against ₹4.6L of fund sales — and "where is the money coming
    # from?" is the most likely question a customer asks about a rebalance.
    # Omitted entirely when nothing is sold, to keep the pack lean.
    _breakdown = getattr(
        getattr(response, "practical_allocation", None), "corpus_breakdown", None
    )
    _excess_stocks = float(
        getattr(_breakdown, "excess_direct_stocks_inr", 0) or 0
    )
    if _excess_stocks > 0:
        pack["direct_stock_sale_inr"] = _excess_stocks
        pack["direct_stock_sale_indian"] = format_inr_indian(_excess_stocks)

    # The ideal (goals + risk) split, shipped ONLY on the first/compute answer
    # (include_ideal) so it can reconcile chat with the allocation tab. It is
    # withheld from follow-up/tilt turns: comparing a tilt against the ideal shifts
    # the baseline away from the recommended plan the customer was just shown and
    # reads as a contradiction — chat always contrasts against the practical plan.
    if include_ideal:
        ideal_mix = ideal_asset_class_mix_pct(response)
        if ideal_mix is not None:
            pack["ideal_asset_class_mix_pct"] = {
                cls: round(value) for cls, value in ideal_mix.items()
            }

    if tax_rules is not None:
        pack["tax_rules"] = tax_rules
    if more_holdings_count > 0:
        pack["more_holdings_count"] = more_holdings_count
    if goal_buckets:
        pack["goal_buckets"] = goal_buckets
    if constraint_impact is not None:
        pack["constraint_impact"] = constraint_impact
    if is_rerun:
        pack["is_rerun"] = True
    if fund_house_view:
        # Prozpr's own market stance (Prozpr-only slice — no fund house is named).
        # Frames WHY the trades make sense; never overrides the computed numbers.
        pack["fund_house_view"] = fund_house_view
    return pack


async def _user_has_mf_holdings(db: AsyncSession, user_id: uuid.UUID) -> bool:
    from app.domains.mutual_funds.models.mf_transaction import MfTransaction

    row = (
        await db.execute(
            select(MfTransaction.id).where(MfTransaction.user_id == user_id).limit(1)
        )
    ).first()
    return row is not None


async def _load_cached_allocation(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> tuple[Optional[GoalAllocationOutput], Optional[uuid.UUID]]:
    """Latest asset-allocation run ≤ 90 days old → (parsed output, run_id) or (None, None).

    Looks up the most recent ``AssetAllocationRun`` for the user's portfolio,
    then loads the full ``GoalAllocationOutput`` from the corresponding
    ``PortfolioAllocationSnapshot`` (IDEAL kind) created alongside it.
    """
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=ALLOCATION_TTL_DAYS)

    run = (
        await db.execute(
            select(AssetAllocationRun)
            .where(AssetAllocationRun.portfolio_id == portfolio.id)
            .where(AssetAllocationRun.created_at >= cutoff)
            .order_by(desc(AssetAllocationRun.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        return None, None

    snap = (
        await db.execute(
            select(PortfolioAllocationSnapshot)
            .where(PortfolioAllocationSnapshot.user_id == run.user_id)
            .where(
                PortfolioAllocationSnapshot.snapshot_kind == PortfolioSnapshotKind.IDEAL
            )
            .where(
                PortfolioAllocationSnapshot.created_at
                >= run.created_at - timedelta(seconds=30)
            )
            .where(
                PortfolioAllocationSnapshot.created_at
                <= run.created_at + timedelta(seconds=30)
            )
            .order_by(desc(PortfolioAllocationSnapshot.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is None:
        return None, None

    payload = (snap.allocation or {}).get("goal_allocation_output")
    if not payload:
        return None, None
    try:
        return GoalAllocationOutput.model_validate(payload), run.id
    except Exception as exc:
        logger.warning("Cached allocation parse failed (%s); ignoring cache", exc)
        return None, None


async def compute_rebalancing_result(
    user,
    user_question: str,
    *,
    db: AsyncSession,
    acting_user_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID],
    persist: bool = True,
    origin: Optional[str] = None,
    force_fresh_allocation: bool = False,
    chat_ctx: "TurnContext | None" = None,
    progress: Optional[Callable[[float, str], Awaitable[None]]] = None,
) -> RebalancingRunOutcome:
    """Top-level orchestrator: cache → builder → engine → persist → format.

    ``progress`` (optional) is awaited at each real stage boundary with
    (percent, customer-facing message) — the Invest page's compute endpoint
    passes a writer so its progress poller can show the live pipeline stage.
    The chat path passes nothing and is unchanged.

    When ``persist=False`` (counterfactual_explore path), the engine still
    runs and reads from the database (holdings, NAVs, metadata, cached
    allocation), but the recommendation row and the chat-ai-module-runs
    telemetry write are skipped. Returns the same outcome shape;
    ``recommendation_id`` is None.

    When ``force_fresh_allocation=True``, the AA cache lookup is skipped and
    AA is always re-run inline. Used when the chat layer has set chat_ctx
    overrides (e.g., ``additional_cash_inr``) that the cached AA result
    wouldn't reflect.
    """
    trace_line("module: rebalancing — start")

    # Stage messages are customer-facing: describe the benefit, never the
    # mechanics (no engine/strategy internals — ranks, caps, tax lots, caches).
    if progress:
        await progress(5, "Reviewing your portfolio & profile…")

    if getattr(user, "date_of_birth", None) is None:
        return RebalancingRunOutcome(
            response=None,
            blocking_message=_MSG_MISSING_DOB,
        )

    if not await _user_has_mf_holdings(db, acting_user_id):
        return RebalancingRunOutcome(
            response=None,
            blocking_message=_MSG_NO_HOLDINGS,
        )

    if chat_ctx is None:
        from app.domains.ai_engine.turn_context import (
            TurnContext,
        )  # lazy: avoids ai_bridge ↔ chat_core cycle at import time

        chat_ctx = TurnContext(
            user_ctx=user,
            user_question=user_question,
            conversation_history=[],
            client_context=None,
            session_id=chat_session_id or uuid.uuid4(),
            db=db,
            effective_user_id=acting_user_id,
            last_agent_runs={},
            active_intent=None,
            chat_overrides=None,
        )

    if progress:
        await progress(12, "Designing your personalised target mix…")

    if force_fresh_allocation:
        # Counterfactual scenarios with AA-affecting overrides: skip cache.
        cached_output = None
        source_allocation_id: Optional[uuid.UUID] = None
        used_cache = False
    else:
        cached_output, source_allocation_id = await _load_cached_allocation(
            db,
            acting_user_id,
        )
        used_cache = cached_output is not None
    allocation_snapshot_id: Optional[uuid.UUID] = None

    if cached_output is None:
        trace_line(
            "rebalancing: allocation cache miss/stale — running allocation inline",
        )
        if progress:
            await progress(18, "Designing your personalised target mix…")
        alloc_outcome: AllocationRunOutcome = await compute_allocation_result(
            user,
            user_question,
            db=db,
            persist_recommendation=True,
            acting_user_id=acting_user_id,
            chat_session_id=chat_session_id,
            spine_mode="rebalance_chained",
            chat_ctx=chat_ctx,
        )
        if alloc_outcome.blocking_message is not None:
            return RebalancingRunOutcome(
                response=None,
                blocking_message=alloc_outcome.blocking_message,
            )
        if alloc_outcome.result is None:
            return RebalancingRunOutcome(
                response=None,
                blocking_message=_MSG_ENGINE_ERROR,
            )
        cached_output = alloc_outcome.result
        source_allocation_id = alloc_outcome.asset_allocation_run_id
        allocation_snapshot_id = alloc_outcome.allocation_snapshot_id

    if progress:
        await progress(58, "Comparing your investments with your target…")

    try:
        request, debug = await build_rebalancing_input_for_user(
            chat_ctx,
            cached_output,
        )
    except Exception as exc:
        logger.exception("rebalancing input builder failed: %s", exc)
        return RebalancingRunOutcome(
            response=None,
            blocking_message=_MSG_UNPRICEABLE,
        )

    trace_line(f"rebalancing input debug: {debug}")

    if progress:
        await progress(70, "Preparing your recommendations…")

    try:
        response: RebalancingComputeResponse = await asyncio.to_thread(
            run_rebalancing,
            request,
        )
    except Exception as exc:
        logger.exception("run_rebalancing failed: %s", exc)
        return RebalancingRunOutcome(
            response=None,
            blocking_message=_MSG_ENGINE_ERROR,
        )

    # Goal-tied bucket block — derived once from the AA output that drove this
    # rebalance, persisted alongside the response so follow-up turns
    # (narrate / educate) see the same goal context.
    #
    # Trade-level `{goal}` placeholder substitution happens inside the
    # engine (see `Rebalancing.rationales.substitute_goal_placeholders`),
    # so `response.trade_list` already carries customer-facing final text.
    try:
        goal_buckets = build_goal_buckets_block(cached_output)
    except Exception as exc:
        logger.warning("goal_buckets_build_failed (non-fatal): %s", exc)
        goal_buckets = None

    rec_id: Optional[uuid.UUID] = None
    if persist:
        if progress:
            await progress(90, "Finalising your plan…")
        rec_id = await persist_rebalancing_recommendation(
            db,
            acting_user_id,
            response,
            chat_session_id=chat_session_id,
            source_allocation_run_id=source_allocation_id,
            used_cached_allocation=used_cache,
            user_question=user_question,
            origin=origin,
        )

        try:
            await record_ai_module_run(
                db,
                user_id=acting_user_id,
                session_id=chat_session_id,
                module="rebalancing",
                reason="full_pipeline_run",
                intent_detected="rebalancing",
                spine_mode=None,
                input_payload=request.model_dump(mode="json"),
                output_payload={
                    "rebalancing_response": response.model_dump(mode="json"),
                    "goal_buckets": goal_buckets,
                    "correlation_ids": {
                        "recommendation_id": str(rec_id),
                        "source_allocation_id": (
                            str(source_allocation_id) if source_allocation_id else None
                        ),
                    },
                },
                emit_standard_log=False,
            )
        except Exception as exc:
            logger.warning("ai_module_telemetry skipped (non-fatal): %s", exc)

    formatted = build_fallback_rebal_brief(
        response,
        used_cached_allocation=used_cache,
    )

    return RebalancingRunOutcome(
        response=response,
        formatted_text=formatted,
        recommendation_id=rec_id,
        allocation_snapshot_id=allocation_snapshot_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
        goal_buckets=goal_buckets,
    )
