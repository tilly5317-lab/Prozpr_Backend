"""
orchestrator.py — AllocationOrchestrator
=========================================
The central controller for the Prozper allocation advisor pipeline.
Coordinates all steps from raw client input to a final allocation response.

HOW IT WORKS (7-step pipeline)
--------------------------------
Step 1  Load fund view
        Reads data/fund_view.txt — the fund house's current monthly market outlook.
        This drives the LLM's market-aware allocation decisions.

Step 2  Receive client profile
        A ClientProfile object (age, risk_profile, investment_horizon_years, goals, income).

Step 3  Generate ideal allocation  [calls Claude Haiku]
        Three phases:
          Phase A — Compute guardrail bounds
                    Reads the YAML rule matrix from skills/guardrails.md and applies
                    age-bracket + risk-profile rules to produce hard min/max limits
                    per asset class (GuardrailBounds).
          Phase B — Call ideal_allocation skill
                    Passes fund view + bounds + client profile to Claude Haiku via
                    the skills/ideal_allocation.md skill. Returns a {min, max} range
                    per asset class (IdealAllocation).
          Phase C — Validate + repair
                    Checks bounds and midpoint sum (99–101%). If the model drifts,
                    ranges are clipped to guardrails and, if needed, midpoints are
                    snapped to a feasible 100% blend (same geometry as fallback),
                    while keeping the model’s reasoning when possible.

Step 4  Load current portfolio (optional)
        A Portfolio object (% per asset class) if the client already has investments.
        If None, the client is treated as new.

Step 5  Compute delta (optional)
        If a current portfolio exists, DeltaCalculator compares it against the ideal
        allocation midpoints to determine what needs to increase / decrease / hold.
        Skipped for new clients.

Step 6  Generate recommendation  [calls Claude Haiku]
        Passes fund view, client profile, ideal allocation, current portfolio, and delta
        to the skills/recommendation.md skill. Returns a narrative, action items
        (what to buy/sell), confidence level, and disclaimers.

Step 7  Format response
        ResponseFormatter assembles all components into a final AllocationResponse object.

GUARDRAIL RULES
----------------
All rules live in skills/guardrails.md (YAML front matter).
To change any limit, edit only that file — no Python changes needed.

ADDING A NEW SKILL
-------------------
Create a new .md file in skills/ with YAML front matter + ## System Prompt + ## User Prompt.
Then call SkillExecutor(skills_dir / "your_skill.md", llm_client) here and invoke it.
"""

import yaml
from pathlib import Path
from .common.llm_client import LLMClient
from .utilities.fund_view_loader import FundViewLoader
from .utilities.delta_calculator import DeltaCalculator
from .utilities.response_formatter import ResponseFormatter
from .skills.executor import SkillExecutor
from .schemas.client_profile import ClientProfile
from .schemas.portfolio import Portfolio
from .schemas.guardrail_bounds import GuardrailBounds, AssetBound
from .schemas.allocation import AssetRange, IdealAllocation
from .schemas.allocation_response import AllocationResponse
from .schemas.recommendation import Recommendation


class GuardrailViolationError(Exception):
    def __init__(self, allocation, bounds):
        self.allocation = allocation
        self.bounds = bounds
        super().__init__(f"Allocation {allocation} violates bounds {bounds}")


class AllocationOrchestrator:
    def __init__(self, llm_client: LLMClient):
        module_root = Path(__file__).parent
        self.fund_view_loader = FundViewLoader(module_root.parent / "data" / "fund_view.txt")
        self._guardrail_rules = self._load_guardrails(module_root / "skills" / "guardrails.md")
        skills_dir = module_root / "skills"
        self.ideal_skill = SkillExecutor(skills_dir / "ideal_allocation.md", llm_client)
        self.rec_skill = SkillExecutor(skills_dir / "recommendation.md", llm_client)
        self.delta_calculator = DeltaCalculator()
        self.formatter = ResponseFormatter()

    # ── Guardrails ────────────────────────────────────────────────────────────

    def _load_guardrails(self, path: Path) -> dict:
        content = path.read_text()
        yaml_block = content.split("---")[1]
        return yaml.safe_load(yaml_block)

    def _age_bracket(self, age: int) -> str:
        if age < 30:
            return "<30"
        elif age <= 45:
            return "30-45"
        elif age <= 60:
            return "46-60"
        else:
            return ">60"

    def _compute_bounds(self, client_profile: ClientProfile) -> GuardrailBounds:
        rules = self._guardrail_rules
        bracket = self._age_bracket(client_profile.age)
        row = rules["age_brackets"][bracket][client_profile.risk_profile]
        splits = rules["equity_splits"]
        caps = rules["small_cap_risk_caps"]

        equity_min = row["equity_min"]
        equity_max = row["equity_max"]

        large_cap_min = round(equity_min * splits["large_cap"]["min_factor"], 1)
        large_cap_max = round(equity_max * splits["large_cap"]["max_factor"], 1)
        mid_cap_min = round(equity_min * splits["mid_cap"]["min_factor"], 1)
        mid_cap_max = round(equity_max * splits["mid_cap"]["max_factor"], 1)
        small_cap_min = round(equity_min * splits["small_cap"]["min_factor"], 1)
        small_cap_max = round(equity_max * splits["small_cap"]["max_factor"], 1)

        cap_factor = caps[client_profile.risk_profile]
        small_cap_max = min(small_cap_max, round(equity_max * cap_factor, 1))

        return GuardrailBounds(
            large_cap=AssetBound(min_pct=large_cap_min, max_pct=large_cap_max),
            mid_cap=AssetBound(min_pct=mid_cap_min, max_pct=mid_cap_max),
            small_cap=AssetBound(min_pct=small_cap_min, max_pct=small_cap_max),
            debt=AssetBound(min_pct=float(row["debt_min"]), max_pct=float(row["debt_max"])),
            gold=AssetBound(min_pct=float(row["gold_min"]), max_pct=float(row["gold_max"])),
        )

    def _midpoint_total(self, ideal: IdealAllocation) -> float:
        return sum(
            (getattr(ideal, a).min + getattr(ideal, a).max) / 2
            for a in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]
        )

    def _validation_failure_detail(self, ideal: IdealAllocation, bounds: GuardrailBounds) -> str:
        parts: list[str] = []
        tol = self._guardrail_rules["validation"]
        for asset in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]:
            r = getattr(ideal, asset)
            b = getattr(bounds, asset)
            if r.min < b.min_pct or r.max > b.max_pct:
                parts.append(f"{asset} [{r.min:g},{r.max:g}] outside [{b.min_pct:g},{b.max_pct:g}]")
        total = self._midpoint_total(ideal)
        if not (tol["sum_min"] <= total <= tol["sum_max"]):
            parts.append(f"midpoint sum {total:.1f}% not in [{tol['sum_min']},{tol['sum_max']}]")
        return "; ".join(parts) if parts else "unknown"

    def _validate(self, allocation: IdealAllocation, bounds: GuardrailBounds) -> bool:
        tol = self._guardrail_rules["validation"]
        for asset in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]:
            r = getattr(allocation, asset)
            b = getattr(bounds, asset)
            if r.min < b.min_pct or r.max > b.max_pct:
                return False
        total = self._midpoint_total(allocation)
        return tol["sum_min"] <= total <= tol["sum_max"]

    def _clip_range_to_bounds(self, r: AssetRange, b: AssetBound) -> AssetRange:
        """Intersect [r.min, r.max] with [b.min_pct, b.max_pct]; collapse if disjoint."""
        lo = max(r.min, b.min_pct)
        hi = min(r.max, b.max_pct)
        if lo <= hi:
            return AssetRange(min=lo, max=hi)
        m = (r.min + r.max) / 2
        m = max(b.min_pct, min(b.max_pct, m))
        return AssetRange(min=m, max=m)

    def _clip_ideal_to_bounds(self, ideal: IdealAllocation, bounds: GuardrailBounds) -> IdealAllocation:
        return IdealAllocation(
            large_cap=self._clip_range_to_bounds(ideal.large_cap, bounds.large_cap),
            mid_cap=self._clip_range_to_bounds(ideal.mid_cap, bounds.mid_cap),
            small_cap=self._clip_range_to_bounds(ideal.small_cap, bounds.small_cap),
            debt=self._clip_range_to_bounds(ideal.debt, bounds.debt),
            gold=self._clip_range_to_bounds(ideal.gold, bounds.gold),
            reasoning=ideal.reasoning,
        )

    def _repair_ideal_allocation(self, ideal: IdealAllocation, bounds: GuardrailBounds) -> IdealAllocation:
        """
        Clip each asset to guardrails. If midpoint sum is still not 99–101%, snap to
        the feasible diagonal blend (same as deterministic fallback) but keep model reasoning.
        """
        clipped = self._clip_ideal_to_bounds(ideal, bounds)
        if self._validate(clipped, bounds):
            return clipped
        targets = self._feasible_midpoints(bounds)
        note = (
            " [Targets numerically normalized to sum to 100% within guardrails—"
            "either midpoint total was outside 99–101% or ranges needed clipping.]"
        )
        base = (ideal.reasoning or "").strip()
        return self._ideal_from_point_targets(targets, reasoning=base + note)

    def _feasible_midpoints(self, bounds: GuardrailBounds) -> dict[str, float]:
        """
        Pick per-asset % targets that lie inside each bound interval and sum to exactly 100.

        Uses the diagonal from all-min to all-max: x_i = low_i + t*(high_i - low_i) with
        t = (100 - sum(lows)) / sum(highs - lows). This is always feasible when
        sum(lows) <= 100 <= sum(highs), and the midpoint sum is exactly 100 by construction
        (fixes LLM outputs that respect per-asset bounds but sum to ~90–95%).
        """
        assets = ["large_cap", "mid_cap", "small_cap", "debt", "gold"]
        lows = [getattr(bounds, a).min_pct for a in assets]
        highs = [getattr(bounds, a).max_pct for a in assets]
        low_sum = sum(lows)
        high_sum = sum(highs)
        range_sum = sum(highs[i] - lows[i] for i in range(5))
        if range_sum <= 0:
            raise ValueError("Invalid guardrail bounds: no range")
        if low_sum > 100 or high_sum < 100:
            raise ValueError("Guardrail bounds cannot contain a 100% allocation")
        t = (100.0 - low_sum) / range_sum
        t = max(0.0, min(1.0, t))
        return {assets[i]: lows[i] + t * (highs[i] - lows[i]) for i in range(5)}

    def _ideal_from_point_targets(self, targets: dict[str, float], reasoning: str) -> IdealAllocation:
        """Build IdealAllocation with min=max at each target (valid midpoint sum = 100)."""
        def ar(name: str) -> AssetRange:
            v = targets[name]
            return AssetRange(min=v, max=v)

        return IdealAllocation(
            large_cap=ar("large_cap"),
            mid_cap=ar("mid_cap"),
            small_cap=ar("small_cap"),
            debt=ar("debt"),
            gold=ar("gold"),
            reasoning=reasoning,
        )

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def _run_ideal_allocation(
        self, fund_view: str, client_profile: ClientProfile
    ) -> tuple[IdealAllocation, dict]:
        # Phase A
        bounds = self._compute_bounds(client_profile)
        print(f"  → Phase A: Guardrail bounds computed ✓")

        # Phase B
        data, usage = await self.ideal_skill.run(
            fund_view=fund_view,
            bounds=bounds.model_dump_json(indent=2),
            client_profile=client_profile.model_dump_json(indent=2),
            strict_note="",
        )
        ideal = IdealAllocation(**data)
        print(f"  → Phase B: Calling Claude Haiku... ✓ (tokens: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out)")

        # Phase C — clip + snap (avoids a second LLM call that often still misses 99–101%)
        if not self._validate(ideal, bounds):
            print(f"  → Phase C: Model output failed checks: {self._validation_failure_detail(ideal, bounds)}")
            print("  → Phase C: Repairing (clip each sleeve to bounds, then feasible 100% blend if needed)...")
            ideal = self._repair_ideal_allocation(ideal, bounds)
        if not self._validate(ideal, bounds):
            print("  → Phase C: Repair unexpectedly insufficient — applying hard fallback.")
            targets = self._feasible_midpoints(bounds)
            ideal = self._ideal_from_point_targets(
                targets,
                reasoning=(
                    "Allocation set to the guardrail-feasible blend that sums to 100%; "
                    "automatic repair could not produce a valid range model."
                ),
            )
            if not self._validate(ideal, bounds):
                raise GuardrailViolationError(ideal, bounds)

        total = self._midpoint_total(ideal)
        print(f"  → Phase C: Guardrails OK ✓ (midpoint sum={total:.0f}%, all sleeves within bounds)")
        return ideal, usage

    async def run(
        self, client_profile: ClientProfile, current_portfolio: Portfolio | None
    ) -> AllocationResponse:
        # Step 1
        fund_view = self.fund_view_loader.load()
        print(f"Step 1: Loading fund view... ✓ ({len(fund_view.split())} words)")

        # Step 2
        print(f"Step 2: Client profile loaded ✓")

        # Step 3
        print(f"Step 3: Generating ideal allocation...")
        ideal, usage3 = await self._run_ideal_allocation(fund_view, client_profile)

        # Step 4
        if current_portfolio:
            print(f"Step 4: Current portfolio loaded ✓")
        else:
            print(f"Step 4: No existing portfolio")

        # Step 5
        delta = self.delta_calculator.compute(ideal, current_portfolio)
        if delta:
            print(f"Step 5: Computing delta... ✓")
        else:
            print(f"Step 5: No portfolio → skipping delta ✓")

        # Step 6
        print(f"Step 6: Generating recommendation...")
        data6, usage6 = await self.rec_skill.run(
            fund_view=fund_view,
            client_profile=client_profile.model_dump_json(indent=2),
            ideal_allocation=ideal.model_dump_json(indent=2),
            current_portfolio=current_portfolio.model_dump_json(indent=2) if current_portfolio else "null",
            delta=delta.model_dump_json(indent=2) if delta else "null",
        )
        rec = Recommendation(**data6)
        print(f"  → Calling Claude Haiku... ✓ (tokens: {usage6['input_tokens']:,} in / {usage6['output_tokens']:,} out)")

        # Step 7
        result = self.formatter.build(ideal, current_portfolio, delta, rec)
        print(f"Step 7: Formatting response... ✓")

        return result
