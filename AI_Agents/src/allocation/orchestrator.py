"""
orchestrator.py — AllocationOrchestrator
=========================================
The central controller for the Prozpr allocation advisor pipeline.
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
          Phase C — Validate + retry
                    Checks that every LLM-returned range stays within guardrail bounds
                    and that the sum of midpoints is within 99–101%.
                    If validation fails, retries once with a stricter prompt.
                    If it fails again, raises GuardrailViolationError.

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
from .schemas.allocation import IdealAllocation
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

    def _validate(self, allocation: IdealAllocation, bounds: GuardrailBounds) -> bool:
        tol = self._guardrail_rules["validation"]
        for asset in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]:
            r = getattr(allocation, asset)
            b = getattr(bounds, asset)
            if r.min < b.min_pct or r.max > b.max_pct:
                return False
        total = sum(
            (getattr(allocation, a).min + getattr(allocation, a).max) / 2
            for a in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]
        )
        return tol["sum_min"] <= total <= tol["sum_max"]

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

        # Phase C
        if not self._validate(ideal, bounds):
            print(f"  → Phase C: Validation FAILED. Retrying with strict prompt...")
            strict = "\n\nCRITICAL: YOU MUST stay within the bounds above. Double-check every number before responding."
            data2, usage2 = await self.ideal_skill.run(
                fund_view=fund_view,
                bounds=bounds.model_dump_json(indent=2),
                client_profile=client_profile.model_dump_json(indent=2),
                strict_note=strict,
            )
            usage["input_tokens"] += usage2["input_tokens"]
            usage["output_tokens"] += usage2["output_tokens"]
            ideal = IdealAllocation(**data2)
            if not self._validate(ideal, bounds):
                raise GuardrailViolationError(ideal, bounds)

        total = sum(
            (getattr(ideal, a).min + getattr(ideal, a).max) / 2
            for a in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]
        )
        print(f"  → Phase C: Validating against bounds... ✓ (midpoint sum={total:.0f}, all within limits)")
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
