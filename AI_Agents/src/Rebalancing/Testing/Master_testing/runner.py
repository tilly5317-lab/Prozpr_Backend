"""Run all 5 profiles end-to-end and dump a single combined JSON + summary.md.

Invocation
----------
    cd AI_Agents/src && python -m Rebalancing.Testing.Master_testing.runner

Outputs to `Rebalancing/Testing/Master_testing/results/`:
  - results.json — single combined file (UI-friendly):
        { schema_version, generated_at, engine_version, engine_config,
          summary[], profiles{} }
    Per-profile entries carry input + holdings + rebalancing_response.
    Goal-allocation output (asset_class_breakdown, bucket_allocations,
    aggregated_subgroups, etc.) is intentionally NOT bundled — in
    production it lives in goal-allocation storage and is served by a
    separate endpoint. The rebalancing module's authoritative output
    is just `rebalancing_response`.
  - summary.md — human-readable comparison table across the 5 profiles.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from goal_based_allocation_pydantic import run_allocation

from Rebalancing import run_rebalancing

from .bridge import build_request, load_ranking, rank1_lookup
from .profiles import HoldingRecord, PROFILES, synth_holdings


SCHEMA_VERSION = "1.0"

# parents: [0]=Master_testing [1]=Testing [2]=Rebalancing [3]=src [4]=AI_Agents
_RANKING_CSV = (
    Path(__file__).resolve().parents[4]
    / "Reference_docs" / "Prozpr_fund_ranking.csv"
)
_RESULTS_DIR = Path(__file__).resolve().parent / "results"
_RESULTS_FILE = _RESULTS_DIR / "results.json"
_SUMMARY_FILE = _RESULTS_DIR / "summary.md"


# Fields that hold integer rupee amounts in the output (rounded for display).
# Engine keeps full Decimal precision internally; this rounding is presentation-only.
_MONEY_KEYS = frozenset({
    # FundRowAfterStepN
    "target_amount_pre_cap",
    "present_allocation_inr", "invested_cost_inr",
    # Subgroup-level aggregates (computed in runner)
    "goal_target_inr", "current_holding_inr",
    "suggested_final_holding_inr", "rebalance_inr",
    "st_value_inr", "st_cost_inr", "lt_value_inr", "lt_cost_inr",
    "final_target_amount", "holding_after_initial_trades", "final_holding_amount",
    "diff",
    "stcg_amount", "ltcg_amount", "exit_load_amount",
    "pass1_buy_amount", "pass1_underbuy_amount",
    "pass1_sell_amount", "pass1_undersell_amount",
    "pass1_sell_lt_amount", "pass1_realised_ltcg",
    "pass1_sell_st_amount", "pass1_realised_stcg",
    "stcg_budget_remaining_after_pass1", "pass1_sell_amount_no_stcg_cap",
    "pass1_undersell_due_to_stcg_cap", "pass1_blocked_stcg_value",
    "stcg_offset_amount", "pass2_sell_amount", "pass2_undersell_amount",
    # Totals
    "total_buy_inr", "total_sell_inr", "net_cash_flow_inr",
    "total_stcg_realised", "total_ltcg_realised", "total_stcg_net_off",
    "total_tax_estimate_inr", "total_exit_load_inr",
    "unrebalanced_remainder_inr",
    # Trade / metadata / holdings / inputs
    "amount_inr",
    "request_corpus_inr", "ltcg_annual_exemption_inr",
    "corpus_inr", "present_inr",
})

# Fields that hold a percentage in the output (kept at 2 decimal places).
_PCT_KEYS = frozenset({
    "max_pct",
    "target_pre_cap_pct", "target_own_capped_pct", "final_target_pct",
    "exit_load_pct",
    "effective_tax_rate_pct",
    "rebalance_min_change_pct",
    "multi_fund_cap_pct", "others_fund_cap_pct",
    "stcg_rate_equity_pct", "ltcg_rate_equity_pct",
})


def _to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _normalize_numbers(obj):
    """In-place: round money keys to int, pct keys to 2 decimals."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in _MONEY_KEYS:
                n = _to_number(v)
                if n is not None:
                    obj[k] = int(round(n))
                    continue
            if k in _PCT_KEYS:
                n = _to_number(v)
                if n is not None:
                    obj[k] = round(n, 2)
                    continue
            _normalize_numbers(v)
    elif isinstance(obj, list):
        for item in obj:
            _normalize_numbers(item)
    return obj


def _decimal_default(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"Cannot serialise {type(o).__name__}")


def _strip_duplicates(payload: dict) -> None:
    """In-place: remove genuine structural duplicates from the JSON dump.

    1. Drop `asset_subgroup` from each `actions[]` entry — it's the parent
       subgroup's key and always identical.
    2. Drop `asset_subgroup` from each `trade_list[]` entry — looked up
       via `isin` from the corresponding action row.
    3. Lift `metadata.knob_snapshot` to top-level `engine_config` IFF all
       profiles share an identical snapshot (typical when a single sweep
       hits the engine with the same env values). Otherwise leave per-profile.

    Engine-internal fields on action rows (step diagnostics, counterfactuals,
    LT/ST split, etc.) stay — they're needed for verifying engine logic.
    """
    profiles = payload.get("profiles", {})

    for prof in profiles.values():
        rb = prof.get("rebalancing_response") or {}
        for sg in rb.get("subgroups") or []:
            for action in sg.get("actions") or []:
                action.pop("asset_subgroup", None)
        for trade in rb.get("trade_list") or []:
            trade.pop("asset_subgroup", None)

    snapshots: list[dict] = []
    for prof in profiles.values():
        meta = (prof.get("rebalancing_response") or {}).get("metadata") or {}
        snap = meta.get("knob_snapshot")
        if snap is not None:
            snapshots.append(snap)

    if snapshots and all(s == snapshots[0] for s in snapshots):
        payload["engine_config"] = snapshots[0]
        for prof in profiles.values():
            meta = (prof.get("rebalancing_response") or {}).get("metadata") or {}
            meta.pop("knob_snapshot", None)

    # Lift engine_version when uniform across profiles.
    versions: list[str] = []
    for prof in profiles.values():
        meta = (prof.get("rebalancing_response") or {}).get("metadata") or {}
        v = meta.get("engine_version")
        if v is not None:
            versions.append(v)
    if versions and all(v == versions[0] for v in versions):
        payload["engine_version"] = versions[0]
        for prof in profiles.values():
            meta = (prof.get("rebalancing_response") or {}).get("metadata") or {}
            meta.pop("engine_version", None)

    # Drop per-profile computed_at — covered by top-level generated_at
    # (timestamps are milliseconds apart in a single sweep).
    for prof in profiles.values():
        meta = (prof.get("rebalancing_response") or {}).get("metadata") or {}
        meta.pop("computed_at", None)

def _holding_to_dict(h: HoldingRecord) -> dict:
    return {
        "isin": h.isin,
        "asset_subgroup": h.asset_subgroup,
        "sub_category": h.sub_category,
        "fund_name": h.fund_name,
        "present_inr": float(h.present_inr),
        "fund_rating": h.fund_rating,
        "is_recommended": h.is_recommended,
    }


def run_all_profiles() -> dict:
    _RESULTS_DIR.mkdir(exist_ok=True)
    ranking = load_ranking(_RANKING_CSV)
    r1 = rank1_lookup(ranking)

    summary: list[dict] = []
    profiles_payload: dict[str, dict] = {}

    for name, profile in PROFILES.items():
        alloc_out = run_allocation(profile)
        holdings = synth_holdings(profile, alloc_out, r1)
        request = build_request(profile, alloc_out, holdings, ranking)
        response = run_rebalancing(request)

        t = response.totals
        summary.append({
            "name": name,
            "corpus_inr": float(profile.total_corpus),
            "tax_regime": profile.tax_regime,
            "rows_count": t.rows_count,
            "total_buy_inr": float(t.total_buy_inr),
            "total_sell_inr": float(t.total_sell_inr),
            "total_stcg_realised": float(t.total_stcg_realised),
            "total_ltcg_realised": float(t.total_ltcg_realised),
            "total_tax_estimate_inr": float(t.total_tax_estimate_inr),
            "total_exit_load_inr": float(t.total_exit_load_inr),
            "trades_count": len(response.trade_list),
            "warnings_count": len(response.warnings),
            "unrebalanced_remainder_inr": float(t.unrebalanced_remainder_inr),
        })

        # Engine response already carries `subgroups` (with action rows
        # filtered) and rationale strings on each trade — drop the full
        # `rows` audit-trail from the JSON for a slimmer UI feed.
        resp_dict = response.model_dump(mode="json")
        resp_dict.pop("rows", None)

        # `allocation_output` is intentionally NOT bundled. In production
        # it lives in goal-allocation storage and is served by a separate
        # endpoint; the rebalancing module's authoritative output is
        # `rebalancing_response`. Engine target per subgroup is already
        # echoed in actions[].target_amount_pre_cap, so debug context is
        # self-contained.
        profiles_payload[name] = {
            "input": profile.model_dump(mode="json"),
            "holdings": [_holding_to_dict(h) for h in holdings],
            "rebalancing_response": resp_dict,
        }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "profiles": profiles_payload,
    }

    _normalize_numbers(payload)
    _strip_duplicates(payload)

    # Reorder top-level keys so versioning + config sit between the run
    # timestamp and the bulky data blocks.
    if "engine_config" in payload or "engine_version" in payload:
        new_payload: dict = {
            "schema_version": payload["schema_version"],
            "generated_at":   payload["generated_at"],
        }
        if "engine_version" in payload:
            new_payload["engine_version"] = payload["engine_version"]
        if "engine_config" in payload:
            new_payload["engine_config"] = payload["engine_config"]
        new_payload["summary"]  = payload["summary"]
        new_payload["profiles"] = payload["profiles"]
        payload = new_payload

    with open(_RESULTS_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=_decimal_default)

    md = [
        "# Rebalancing — 5-profile sweep summary",
        "",
        "| Profile | Corpus (₹) | Tax | Rows | Buy (₹) | Sell (₹) | STCG (₹) | LTCG (₹) | Tax (₹) | Exit Load (₹) | Trades | Warnings | Unrebal. |",
        "|---|---:|:--:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        md.append(
            f"| {r['name']} | {r['corpus_inr']:,} | {r['tax_regime']} "
            f"| {r['rows_count']} | {r['total_buy_inr']:,} | {r['total_sell_inr']:,} "
            f"| {r['total_stcg_realised']:,} | {r['total_ltcg_realised']:,} "
            f"| {r['total_tax_estimate_inr']:,} | {r['total_exit_load_inr']:,} "
            f"| {r['trades_count']} | {r['warnings_count']} "
            f"| {r['unrebalanced_remainder_inr']:,} |"
        )
    md.append("")
    md.append("Generated by `Rebalancing/Master_testing/runner.py`. "
              "Combined per-profile detail in `results.json`.")
    _SUMMARY_FILE.write_text("\n".join(md))

    return payload


if __name__ == "__main__":
    payload = run_all_profiles()
    print(f"results.json: {_RESULTS_FILE}")
    print(f"summary.md:   {_SUMMARY_FILE}")
    print(f"profiles:     {len(payload['profiles'])}")
