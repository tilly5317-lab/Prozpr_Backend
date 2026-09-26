"""Offline guards for the S2 preference vocabulary on the rebalancing detector:
schema shape, and static prompt scans (the prompt + Field descriptions ARE the
tool schema Haiku sees). No live API."""

from __future__ import annotations

from typing import get_args

from app.domains.rebalancing.services.rebal_engine.chat import (
    _DETECT_REBAL_SYSTEM,
    RebalanceAction,
)


def test_legacy_tilt_fields_are_gone():
    for name in ("tilt_asset_class", "tilt_delta_pp", "tilt_target_pct",
                 "scope_only_asset_classes", "market_cap", "market_cap_heavy"):
        assert name not in RebalanceAction.model_fields, name
    assert "save_preference" not in get_args(RebalanceAction.model_fields["mode"].annotation)


def test_preference_ask_parses_inside_the_action():
    a = RebalanceAction(
        mode="counterfactual_explore",
        preference_asks=[{"target": "small_cap", "level": "heavy"},
                         {"target": "equity", "level": "number", "number": 100}],
    )
    assert [x.target for x in a.preference_asks] == ["small_cap", "equity"]
    assert a.preference_asks[1].number == 100


def test_prompt_documents_preference_asks_and_the_pill():
    assert "preference_asks" in _DETECT_REBAL_SYSTEM
    assert "save_preference" not in _DETECT_REBAL_SYSTEM
    # the pill saves the plan now: the prompt must never teach a "just this once" framing
    assert "just this once" not in _DETECT_REBAL_SYSTEM.lower()


def test_legacy_sleeve_tilt_is_gone_everywhere():
    """S2-R3: the per-turn sleeve tilt is deleted; the S1 human_override step is
    the only preference carrier."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[4]
    tokens = ("asset_class_tilt", "market_cap_tilt", "pure_equity_only",
              "normalize_market_cap_tilt", "_apply_asset_class_tilt", "_apply_market_cap_tilt",
              "scope_only_asset_classes", "market_cap_heavy")
    offenders = []
    for root in (repo / "app", repo / "AI_Agents" / "src"):
        for path in root.rglob("*.py"):
            rel = path.relative_to(repo).as_posix()
            if "/tests/" in rel or "/Testing/" in rel or "/archive/" in rel:
                continue
            if any(t in path.read_text(errors="ignore") for t in tokens):
                offenders.append(rel)
    assert offenders == [], offenders


def test_engine_version_bumped_for_the_tilt_removal():
    from app.domains.ai_engine.common import ensure_ai_agents_path

    ensure_ai_agents_path()
    from Rebalancing.config import ENGINE_VERSION

    assert ENGINE_VERSION != "1.6.0"
