"""Sectioned markdown output for the rebalancing chat reply.

Voice: financially-savvy friend, not advisor. Plain language, no compliance
boilerplate, contractions OK. All copy lives in this file so tone iterations
don't touch the structured data path.

Layout:
  1. Optional lead line (when allocation was refreshed this turn).
  2. Header (move count + corpus).
  3. Top summary table: one row per SEBI sub_category with current/target/plan.
  4. Per sub_category section: header + buy table + sell/exit table (whichever
     are non-empty).
  5. Trade-offs line (taxes + exit loads + realised gains) — omitted when zero.
  6. Heads-up bullet list — only when warnings present.
  7. Closing nudge.

Grouping is by ``(asset_subgroup, sub_category)`` not just ``sub_category``:
within ``short_debt`` for example, ``Ultra Short Duration Fund`` and ``Low
Duration Fund`` should render as separate sections so users can act on them
independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
    RebalancingComputeResponse,
    RebalancingWarning,
    WarningCode,
)


_LEAD_REFRESHED = (
    "_First I redid your asset mix from your goals, then worked out the trades to get there._"
)
_CLOSING = (
    "_Worth a sanity check on exit loads and tax before you pull the trigger._"
)


def _fmt_inr(amount: Decimal | float | int) -> str:
    return f"₹{Decimal(amount):,.0f}"


def _sell_total(row: FundRowAfterStep5) -> Decimal:
    return (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))


@dataclass
class _Bucket:
    """Aggregated view of a single (asset_subgroup, sub_category) pair."""

    asset_subgroup: str
    sub_category: str
    actions: list[FundRowAfterStep5]

    @property
    def current(self) -> Decimal:
        return sum((r.present_allocation_inr for r in self.actions), Decimal(0))

    @property
    def buy_total(self) -> Decimal:
        return sum(
            ((r.pass1_buy_amount or Decimal(0)) for r in self.actions),
            Decimal(0),
        )

    @property
    def sell_total(self) -> Decimal:
        return sum((_sell_total(r) for r in self.actions), Decimal(0))

    @property
    def planned_final(self) -> Decimal:
        return self.current - self.sell_total + self.buy_total


def _bucketise(response: RebalancingComputeResponse) -> list[_Bucket]:
    """Roll the engine's per-subgroup actions up into one bucket per
    ``(asset_subgroup, sub_category)`` pair, dropping phantom rows
    (no holding and no buy/sell)."""
    by_key: dict[tuple[str, str], _Bucket] = {}
    for s in response.subgroups:
        for row in s.actions:
            if (
                row.present_allocation_inr <= 0
                and (row.pass1_buy_amount or Decimal(0)) <= 0
                and _sell_total(row) <= 0
            ):
                continue
            key = (s.asset_subgroup, row.sub_category)
            bucket = by_key.get(key)
            if bucket is None:
                bucket = _Bucket(
                    asset_subgroup=s.asset_subgroup,
                    sub_category=row.sub_category,
                    actions=[],
                )
                by_key[key] = bucket
            bucket.actions.append(row)
    # Stable order: highest current first, then highest planned, then label.
    return sorted(
        by_key.values(),
        key=lambda b: (-b.current, -b.planned_final, b.sub_category),
    )


def _bucket_target(
    b: _Bucket,
    all_buckets: list[_Bucket],
    response: RebalancingComputeResponse,
) -> Decimal:
    """Approximate per-bucket target.

    Engine targets are at ``asset_subgroup`` level; when one subgroup contains
    multiple sub_categories, we pro-rate the subgroup's goal target across its
    sub_category buckets in proportion to each bucket's planned-final amount,
    falling back to equal-share when planned-finals are all zero.
    """
    parent = next(
        (s for s in response.subgroups if s.asset_subgroup == b.asset_subgroup),
        None,
    )
    if parent is None:
        return Decimal(0)
    siblings = [x for x in all_buckets if x.asset_subgroup == b.asset_subgroup]
    if len(siblings) <= 1:
        return parent.goal_target_inr
    total_planned = sum((s.planned_final for s in siblings), Decimal(0))
    if total_planned > 0:
        return parent.goal_target_inr * (b.planned_final / total_planned)
    return parent.goal_target_inr / len(siblings)


def _summary_table(
    buckets: list[_Bucket], response: RebalancingComputeResponse,
) -> list[str]:
    rows: list[str] = []
    rows.append("| Category | Current | Target | Plan |")
    rows.append("| --- | ---: | ---: | ---: |")
    for b in buckets:
        target = _bucket_target(b, buckets, response)
        rows.append(
            f"| {b.sub_category} | {_fmt_inr(b.current)} | "
            f"{_fmt_inr(target)} | {_fmt_inr(b.planned_final)} |"
        )
    return rows


def _action_tables(b: _Bucket) -> list[str]:
    out: list[str] = []
    buys = [r for r in b.actions if (r.pass1_buy_amount or Decimal(0)) > 0]
    sells = [r for r in b.actions if _sell_total(r) > 0]

    if buys:
        out.append("| Buy into | Amount |")
        out.append("| --- | ---: |")
        for r in sorted(buys, key=lambda r: -(r.pass1_buy_amount or Decimal(0))):
            out.append(f"| {r.recommended_fund} | {_fmt_inr(r.pass1_buy_amount)} |")
        out.append("")

    if sells:
        out.append("| Action | From | Amount |")
        out.append("| --- | --- | ---: |")
        for r in sorted(sells, key=lambda r: -_sell_total(r)):
            verb = "Exit" if r.exit_flag else "Trim"
            out.append(f"| {verb} | {r.recommended_fund} | {_fmt_inr(_sell_total(r))} |")
        out.append("")

    return out


def _warning_line(w: RebalancingWarning) -> str:
    code = w.code
    if code == WarningCode.BAD_FUND_DETECTED:
        funds = ", ".join(w.affected_isins) or "a few funds"
        return (
            f"- {funds} aren't on the recommended list anymore — "
            "worth exiting when the tax math works."
        )
    if code == WarningCode.UNREBALANCED_REMAINDER:
        return (
            f"- {w.message} couldn't be placed cleanly under the per-fund caps — "
            "small enough to ignore."
        )
    if code == WarningCode.STCG_BUDGET_BINDING:
        return (
            "- Held back some sells to keep short-term gains under your offset budget."
        )
    if code == WarningCode.NO_HOLDINGS_FOR_RECOMMENDED_FUND:
        funds = ", ".join(w.affected_isins) or "a recommended fund"
        return f"- {funds} on your plan but you don't hold it yet — fresh purchase."
    return f"- {w.message}"


def format_rebalancing_chat_brief(
    response: RebalancingComputeResponse,
    *,
    used_cached_allocation: bool,
) -> str:
    """Render the engine response as a chat-ready markdown brief."""
    out: list[str] = []

    if not used_cached_allocation:
        out.append(_LEAD_REFRESHED)
        out.append("")

    totals = response.totals
    n_trades = (
        totals.funds_to_buy_count
        + totals.funds_to_sell_count
        + totals.funds_to_exit_count
    )
    corpus = response.metadata.request_corpus_inr
    out.append(
        f"Here's how I'd rebalance — {n_trades} moves on a corpus of about {_fmt_inr(corpus)}."
    )
    out.append("")

    buckets = _bucketise(response)
    if buckets:
        out.extend(_summary_table(buckets, response))
        out.append("")

        for b in buckets:
            target = _bucket_target(b, buckets, response)
            out.append(
                f"**{b.sub_category}** — current {_fmt_inr(b.current)}, "
                f"target {_fmt_inr(target)}, plan {_fmt_inr(b.planned_final)}."
            )
            section = _action_tables(b)
            if section:
                out.extend(section)
            else:
                out.append("- Hold as-is.")
                out.append("")

    if (totals.total_tax_estimate_inr or 0) > 0 or (
        totals.total_exit_load_inr or 0
    ) > 0:
        out.append(
            f"The trade-offs: about {_fmt_inr(totals.total_tax_estimate_inr)} in taxes and "
            f"{_fmt_inr(totals.total_exit_load_inr)} in exit loads, with "
            f"{_fmt_inr(totals.total_stcg_realised)} short-term and "
            f"{_fmt_inr(totals.total_ltcg_realised)} long-term gains realised."
        )
        out.append("")

    if response.warnings:
        out.append("**A couple of heads-ups:**")
        for w in response.warnings:
            out.append(_warning_line(w))
        out.append("")

    out.append(_CLOSING)
    return "\n".join(out).rstrip() + "\n"
