"""Deterministic markdown tables for ``GoalAllocationOutput`` (DB-parity view)."""

from __future__ import annotations

from typing import Any

from app.domains.ai_engine.common import format_inr_indian

_BUCKET_ORDER = ["emergency", "short_term", "medium_term", "long_term"]


def _inr(amount: Any) -> str:
    return format_inr_indian(_float(amount))


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(value: Any) -> str:
    return f"{_float(value):.1f}%"


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines)


def _doc(output: Any) -> dict[str, Any]:
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json")
    return dict(output) if isinstance(output, dict) else {}


def _subgroup_lists_for_bucket(
    doc: dict[str, Any], bucket_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sub_root = (doc.get("asset_class_breakdown") or {}).get("subgroups") or {}
    if not isinstance(sub_root, dict):
        return [], []

    def _extract(blocks: list[Any]) -> list[dict[str, Any]]:
        for block in blocks:
            if isinstance(block, dict) and str(block.get("bucket") or "").lower() == bucket_key:
                return [x for x in (block.get("subgroups") or []) if isinstance(x, dict)]
        return []

    return (
        _extract(list(sub_root.get("planned") or [])),
        _extract(list(sub_root.get("actual") or [])),
    )


def is_allocation_output_complete(output: Any) -> bool:
    doc = _doc(output)
    if _float(doc.get("grand_total")) <= 0:
        return False
    buckets = [b for b in (doc.get("bucket_allocations") or []) if isinstance(b, dict)]
    if not buckets:
        return False
    planned = (doc.get("asset_class_breakdown") or {}).get("planned") or {}
    if _float(planned.get("equity_total")) + _float(planned.get("debt_total")) <= 0:
        return False
    return bool((doc.get("client_summary") or {}).get("goals"))


def build_allocation_tables_markdown(output: Any) -> str:
    """Full engine output as markdown tables (one section per persisted table)."""
    doc = _doc(output)
    cs = doc.get("client_summary") or {}
    acb = doc.get("asset_class_breakdown") or {}
    planned = acb.get("planned") or {}
    actual = acb.get("actual") or {}
    grand = _float(doc.get("grand_total"))

    lines: list[str] = ["## Ideal allocation (engine tables)", ""]

    lines.extend([
        "### `asset_allocation_runs`",
        "",
        _md_table(
            ["Field", "Value"],
            [
                ["age", cs.get("age")],
                ["occupation", cs.get("occupation")],
                ["effective_risk_score", cs.get("effective_risk_score")],
                ["total_corpus", _inr(cs.get("total_corpus"))],
                ["grand_total", _inr(grand)],
                ["all_amounts_in_multiples_of_100", doc.get("all_amounts_in_multiples_of_100")],
            ],
        ),
        "",
    ])

    goal_rows: list[list[Any]] = []
    for g in cs.get("goals") or []:
        if not isinstance(g, dict):
            continue
        goal_rows.append([
            g.get("goal_name"),
            g.get("time_to_goal_months"),
            _inr(g.get("amount_needed")),
            g.get("goal_priority"),
            g.get("investment_goal"),
        ])
    lines.extend([
        "### `asset_allocation_run_targets`",
        "",
        _md_table(
            ["goal_name", "time_to_goal_months", "amount_needed", "goal_priority", "investment_goal"],
            goal_rows,
        ),
        "",
    ])

    def _aggregate_section(label: str, blk: dict[str, Any]) -> list[str]:
        if not blk:
            return []
        return [
            f"**{label}**",
            "",
            _md_table(
                [
                    "equity_total", "equity_total_pct", "debt_total", "debt_total_pct",
                    "others_total", "others_total_pct",
                ],
                [[
                    _inr(blk.get("equity_total")), _pct(blk.get("equity_total_pct")),
                    _inr(blk.get("debt_total")), _pct(blk.get("debt_total_pct")),
                    _inr(blk.get("others_total")), _pct(blk.get("others_total_pct")),
                ]],
            ),
            "",
        ]

    lines.append("### `asset_allocation_aggregates`")
    lines.append("")
    lines.extend(_aggregate_section("planned", planned))
    lines.extend(_aggregate_section("actual", actual))

    bucket_rows: list[list[Any]] = []
    bucket_goal_rows: list[list[Any]] = []
    split_planned_rows: list[list[Any]] = []
    split_actual_rows: list[list[Any]] = []
    subgroup_rows: list[list[Any]] = []

    buckets_by = {
        str(b.get("bucket", "")).lower(): b
        for b in (doc.get("bucket_allocations") or [])
        if isinstance(b, dict)
    }
    planned_per = {
        str(r.get("bucket", "")).lower(): r
        for r in (planned.get("per_bucket") or [])
        if isinstance(r, dict)
    }
    actual_per = {
        str(r.get("bucket", "")).lower(): r
        for r in (actual.get("per_bucket") or [])
        if isinstance(r, dict)
    }

    for key in _BUCKET_ORDER:
        b = buckets_by.get(key)
        if not b:
            continue
        fi = b.get("future_investment") or {}
        bucket_rows.append([
            key,
            _inr(b.get("total_goal_amount")),
            _inr(b.get("allocated_amount")),
            _inr(fi.get("future_investment_amount")),
            fi.get("message"),
            b.get("rationale"),
        ])
        rationales = b.get("goal_rationales") or {}
        for g in b.get("goals") or []:
            if not isinstance(g, dict):
                continue
            name = g.get("goal_name", "")
            bucket_goal_rows.append([
                key,
                name,
                g.get("time_to_goal_months"),
                _inr(g.get("amount_needed")),
                g.get("goal_priority"),
                g.get("investment_goal"),
                rationales.get(name),
            ])
        pr = planned_per.get(key)
        if pr:
            split_planned_rows.append([
                key,
                _inr(pr.get("equity")), _pct(pr.get("equity_pct")),
                _inr(pr.get("debt")), _pct(pr.get("debt_pct")),
                _inr(pr.get("others")), _pct(pr.get("others_pct")),
            ])
        ar = actual_per.get(key)
        if ar:
            split_actual_rows.append([
                key,
                _inr(ar.get("equity")), _pct(ar.get("equity_pct")),
                _inr(ar.get("debt")), _pct(ar.get("debt_pct")),
                _inr(ar.get("others")), _pct(ar.get("others_pct")),
            ])
        p_sg, a_sg = _subgroup_lists_for_bucket(doc, key)
        by_p = {str(r.get("subgroup")): r for r in p_sg if r.get("subgroup")}
        by_a = {str(r.get("subgroup")): r for r in a_sg if r.get("subgroup")}
        flat = b.get("subgroup_amounts") or {}
        if not isinstance(flat, dict):
            flat = {}
        for sg in sorted(set(by_p) | set(by_a) | set(flat)):
            pr_row = by_p.get(sg, {})
            ar_row = by_a.get(sg, {})
            subgroup_rows.append([
                key,
                sg.replace("_", " "),
                _inr(pr_row.get("amount") if pr_row else flat.get(sg)),
                _pct(pr_row.get("pct_of_bucket")) if pr_row else "—",
                _inr(ar_row.get("amount") if ar_row else flat.get(sg)),
                _pct(ar_row.get("pct_of_bucket")) if ar_row else "—",
            ])

    lines.extend([
        "### `asset_allocation_buckets`",
        "",
        _md_table(
            [
                "bucket_name", "total_goal_amount", "allocated_amount",
                "future_investment_amount", "future_investment_message", "rationale",
            ],
            bucket_rows,
        ),
        "",
        "### `asset_allocation_bucket_run_targets`",
        "",
        _md_table(
            [
                "bucket_name", "goal_name", "time_to_goal_months", "amount_needed",
                "goal_priority", "investment_goal", "goal_rationale",
            ],
            bucket_goal_rows,
        ),
        "",
        "### `asset_allocation_bucket_asset_classes` (planned)",
        "",
        _md_table(
            [
                "bucket_name", "equity", "equity_pct", "debt", "debt_pct",
                "others", "others_pct",
            ],
            split_planned_rows,
        ),
        "",
        "### `asset_allocation_bucket_asset_classes` (actual)",
        "",
        _md_table(
            [
                "bucket_name", "equity", "equity_pct", "debt", "debt_pct",
                "others", "others_pct",
            ],
            split_actual_rows,
        ),
        "",
        "### `asset_allocation_bucket_subgroups`",
        "",
        _md_table(
            [
                "bucket_name", "subgroup", "planned_amount", "planned_pct_of_bucket",
                "actual_amount", "actual_pct_of_bucket",
            ],
            subgroup_rows,
        ),
        "",
    ])

    agg_rows = []
    for row in doc.get("aggregated_subgroups") or []:
        if not isinstance(row, dict):
            continue
        agg_rows.append([
            str(row.get("subgroup", "")).replace("_", " "),
            _inr(row.get("emergency")),
            _inr(row.get("short_term")),
            _inr(row.get("medium_term")),
            _inr(row.get("long_term")),
            _inr(row.get("total")),
        ])
    if agg_rows:
        lines.extend([
            "### `aggregated_subgroups`",
            "",
            _md_table(
                ["subgroup", "emergency", "short_term", "medium_term", "long_term", "total"],
                agg_rows,
            ),
            "",
        ])

    fut_rows = []
    for fi in doc.get("future_investments_summary") or []:
        if not isinstance(fi, dict):
            continue
        fut_rows.append([
            fi.get("bucket"),
            _inr(fi.get("future_investment_amount")),
            fi.get("message"),
        ])
    if fut_rows:
        lines.extend([
            "### `future_investments_summary`",
            "",
            _md_table(["bucket", "future_investment_amount", "message"], fut_rows),
            "",
        ])

    return "\n".join(lines).rstrip() + "\n"
