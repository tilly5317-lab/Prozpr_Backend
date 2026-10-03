"""Rebalancing asset-class breakdown (current vs target) with hybrid look-through.

THE single Equity/Debt/Others rollup for a rebalancing run. Both the Invest-page
"Current vs Target" bars and the rebalancing CHAT facts pack go through
``asset_class_mix_from_rows`` here, so the two surfaces cannot quote different
splits for the same run. They used to own separate rollups: the page mapped each
subgroup to one asset class while chat looked through per fund, which put the
page at 98/1/0 and chat at 95/3/2 on the same holdings. Chat also had no target
mix at all, so when a customer asked what the plan was moving them toward, the
LLM cited the only mix it had — the CURRENT one — and called it the target.

Two rules, and the difference between them is deliberate:

- PER-FUND rows (CURRENT and the plan's TARGET) are looked through on their own
  ``sub_category`` — a held Aggressive Hybrid splits 72.5/17.5/10. A holding parked
  in the ``multi_asset`` subgroup is still just whatever fund it is; it does NOT get
  the sleeve composition.
- FUND-LESS rows — subgroup totals (the GOAL mix, the legacy no-fund-rows fallback)
  and any single row whose ``sub_category`` never resolved — have no category to look
  through, so there the ``multi_asset`` sleeve IS a generic multi-asset allocation the
  engine sized by ``DEFAULT_MULTI_ASSET_COMPOSITION_PCTS`` (65/25/10) and is split by
  that composition. The subgroup's own nominal class would say Equity, which is a
  worse answer than the estimate.

WHY THE TARGET BAR NO LONGER USES THE SLEEVE (2026-09-27). It used to, and that
silently re-labelled money the plan never traded: a customer holding a Flexi Cap
fund (100% equity) and an Aggressive Hybrid inside the ``multi_asset`` subgroup saw
them looked through honestly on the Current bar and forced to 65/25/10 on the Target
bar. On a plan that could not sell either fund, the Target bar invented ~11 points of
Debt and ~4 points of Others out of money that did not move, so the bar matched
neither the customer's goal mix nor what they would actually hold. The sleeve split
guards against an equity-heavy fund filling an UNFILLED sleeve and deleting the
plan's debt — a concern about headroom with no fund attached to it, which is exactly
the subgroup-total case above. Money that is already a named fund is reported as that
fund on both bars.

Only genuinely blended SEBI categories are looked through; the band table lives in
``scheme_classification.ASSET_CLASS_LOOKTHROUGH_WEIGHTS``.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.scheme_classification import (
    ASSET_CLASS_DEBT,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_OTHERS,
    add_to_asset_class_mix,
    asset_class_for_subgroup,
)

ensure_ai_agents_path()
from asset_allocation_pydantic.tables import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS,
)

MULTI_ASSET_SUBGROUP = "multi_asset"

# The engine sizes its multi_asset sleeve by DEFAULT_MULTI_ASSET_COMPOSITION_PCTS
# (equity, debt, others), so fund-less sleeve headroom splits by that same
# composition. Sourced from the engine constant so the two can't drift apart.
_SLEEVE_EQUITY_PCT, _SLEEVE_DEBT_PCT, _SLEEVE_OTHERS_PCT = (
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS
)


# A fund-level row: (asset_subgroup, sub_category, amount_inr). ``sub_category``
# may be None on rows whose metadata never resolved — those fall back to the
# subgroup's nominal asset class.
AssetClassRow = tuple[Optional[str], Optional[str], float]


def asset_class_mix_from_rows(
    rows: Iterable[AssetClassRow],
    *,
    multi_asset_sleeve: bool,
) -> dict[str, float]:
    """THE Equity/Debt/Others rollup. Title-case keys, ₹ amounts.

    The sleeve composition stands in wherever a ``multi_asset`` row has NO fund
    identity to look through — either because the caller says the whole row set is
    fund-less (``multi_asset_sleeve=True``: subgroup totals, unfilled headroom) or
    because that individual row's ``sub_category`` never resolved. A row that names a
    real fund is reported as that fund, because pretending otherwise re-labels money
    the plan never touched; see the module docstring.

    Note the per-row half matters even with ``multi_asset_sleeve=False``: the
    subgroup's nominal asset class for ``multi_asset`` is Equity, so a category-less
    hybrid would otherwise read as 100% equity — worse than the sleeve estimate.
    """
    mix: dict[str, float] = {}
    for asset_subgroup, sub_category, amount in rows:
        amount = float(amount or 0.0)
        no_fund_identity = multi_asset_sleeve or not sub_category
        if no_fund_identity and asset_subgroup == MULTI_ASSET_SUBGROUP:
            mix[ASSET_CLASS_EQUITY] = (
                mix.get(ASSET_CLASS_EQUITY, 0.0) + amount * _SLEEVE_EQUITY_PCT / 100.0
            )
            mix[ASSET_CLASS_DEBT] = (
                mix.get(ASSET_CLASS_DEBT, 0.0) + amount * _SLEEVE_DEBT_PCT / 100.0
            )
            mix[ASSET_CLASS_OTHERS] = (
                mix.get(ASSET_CLASS_OTHERS, 0.0) + amount * _SLEEVE_OTHERS_PCT / 100.0
            )
        else:
            add_to_asset_class_mix(
                mix,
                amount=amount,
                sub_category=sub_category,
                fallback_asset_class=asset_class_for_subgroup(asset_subgroup),
            )
    return mix


def current_mix_from_rows(rows: Iterable[AssetClassRow]) -> dict[str, float]:
    """CURRENT holdings mix — every row looked through on its own sub_category."""
    return asset_class_mix_from_rows(rows, multi_asset_sleeve=False)


def target_mix_from_rows(rows: Iterable[AssetClassRow]) -> dict[str, float]:
    """TARGET (post-trade) mix — every row looked through on its own sub_category.

    Same rule as ``current_mix_from_rows`` on purpose: both bars describe concrete
    funds, so the SAME untraded rupee must land in the same asset class on both.
    See the module docstring for the sleeve override this deliberately drops.
    """
    return asset_class_mix_from_rows(rows, multi_asset_sleeve=False)


def plan_rows_from_run(
    fund_rows: Sequence[Any],
    trades: Sequence[Any],
) -> tuple[list[AssetClassRow], list[AssetClassRow]]:
    """Build the (current, target) row sets for a persisted rebalancing run.

    Target amount per fund is ``present + buys - sells``, which sums to the run's
    ``suggested_final_holding_inr`` total. Do NOT use ``final_target_amount`` — it
    is a per-candidate uncapped target across ranked funds and sums to ~30% more
    than the portfolio.
    """
    present: dict[tuple[Any, Any, Any], float] = {}
    for row in fund_rows:
        key = (row.asset_subgroup, row.sub_category, row.isin)
        present[key] = present.get(key, 0.0) + float(row.present_allocation_inr or 0.0)

    delta: dict[tuple[Any, Any, Any], float] = {}
    for trade in trades:
        key = (trade.asset_subgroup, trade.sub_category, trade.isin)
        amount = abs(float(trade.amount_inr or 0.0))
        is_buy = "buy" in str(getattr(trade.action, "value", trade.action)).lower()
        delta[key] = delta.get(key, 0.0) + (amount if is_buy else -amount)

    current_rows: list[AssetClassRow] = [
        (subgroup, sub_category, amount)
        for (subgroup, sub_category, _isin), amount in present.items()
    ]
    target_rows: list[AssetClassRow] = []
    for key in set(present) | set(delta):
        subgroup, sub_category, _isin = key
        target_rows.append(
            (subgroup, sub_category, present.get(key, 0.0) + delta.get(key, 0.0))
        )
    return current_rows, target_rows


def _subgroup_mix(subgroup_summaries: Iterable[Any], attr: str) -> dict[str, float]:
    """Roll one ₹ column of the subgroup summaries up, sleeve-split.

    Two callers, both of them fund-less by nature: the GOAL mix (subgroup headroom
    the plan has not assigned to funds yet) and the legacy fallback for runs
    persisted without fund rows, where per-fund ``sub_category`` is unavailable and
    no look-through is possible.
    """
    return asset_class_mix_from_rows(
        (
            (
                getattr(summary, "asset_subgroup", None),
                None,
                float(getattr(summary, attr, 0.0) or 0.0),
            )
            for summary in subgroup_summaries
        ),
        multi_asset_sleeve=True,
    )


def target_asset_class_mix(subgroup_summaries: Iterable[Any]) -> dict[str, float]:
    """Rebalancing TARGET (``suggested_final_holding_inr``) as Equity/Debt/Others."""
    return _subgroup_mix(subgroup_summaries, "suggested_final_holding_inr")


def goal_asset_class_mix(subgroup_summaries: Iterable[Any]) -> dict[str, float]:
    """The GOAL mix (``goal_target_inr``) as Equity/Debt/Others.

    What the customer's goals, risk profile and saved preference call for — the
    allocation the engine AIMED at, before holdings reality, lock-ins and the
    short-term-gains rule cut the plan down. Distinct from the target mix, which is
    where this plan can actually land; the two legitimately differ and the Invest
    page shows both so a customer who set 50/50 and sees a 77/20 plan can see why.

    Sleeve-split (``_subgroup_mix``) because a goal target is subgroup headroom with
    no fund attached to it yet.
    """
    return _subgroup_mix(subgroup_summaries, "goal_target_inr")


def run_current_asset_class_mix(subgroup_summaries: Iterable[Any]) -> dict[str, float]:
    """The run's own CURRENT (``current_holding_inr``) as Equity/Debt/Others.

    Uses the same valuation basis the engine sized the plan on, so the
    current-vs-target bars are directly comparable: their totals differ only by
    the plan's net cash flow (≈0), never by a statement-NAV vs today's-NAV gap.
    The ``portfolio_holdings`` rollup (statement NAVs) can sit several percent
    away from the engine's transaction-derived, today's-NAV totals, which made
    the Current bar render visibly shorter than the Target bar.
    """
    return _subgroup_mix(subgroup_summaries, "current_holding_inr")
