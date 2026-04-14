"""
orchestrator.py — PortfolioQueryOrchestrator
=============================================
Handles the portfolio_query intent: answers client questions about their own
portfolio using three context sources — the fund house market outlook, the
client profile, and the client's current portfolio.

HOW IT WORKS (4-step pipeline)
--------------------------------
Step 1  Load fund view
        Reads data/fund_view.txt — the fund house's current monthly market outlook.

Step 2  Format context
        Serialises the client profile, current portfolio, and conversation history
        into strings ready for template injection.

Step 3  Call portfolio_query skill  [calls Claude Haiku]
        Runs portfolio_query.md in this package. The system prompt embeds the
        guardrail rules (from guardrails.md) and instructs the model to:
          - Check if the question is in scope.
          - If out of scope: return guardrail_triggered=true with a redirect message.
          - If in scope: answer factually from the three context sources.

Step 4  Return response
        Parses the JSON response from Claude into a PortfolioQueryResponse object.
        Logs token usage.

GUARDRAIL RULES
----------------
All scope rules live in guardrails.md (pure Markdown).
To change what is in or out of scope, edit only that file — no Python changes needed.

ADDING A NEW SKILL
-------------------
Create a new .md file in this package with YAML front matter + ## System Prompt + ## User Prompt.
Then call SkillExecutor(module_root / "your_skill.md", llm_client) here and invoke it.
"""

from pathlib import Path

from .models import ConversationTurn, PortfolioQueryResponse
from allocation.common.llm_client import LLMClient
from allocation.utilities.fund_view_loader import FundViewLoader
from allocation.skills.executor import SkillExecutor
from allocation.schemas.client_profile import ClientProfile
from allocation.schemas.portfolio import Portfolio


class PortfolioQueryOrchestrator:
    def __init__(self, llm_client: LLMClient):
        module_root = Path(__file__).parent
        data_root = module_root.parent / "data"

        self.fund_view_loader = FundViewLoader(data_root / "fund_view.txt")

        self._guardrail_rules = (module_root / "guardrails.md").read_text()
        self.query_skill = SkillExecutor(module_root / "portfolio_query.md", llm_client)

    def _format_history(self, history: list[ConversationTurn]) -> str:
        if not history:
            return "(No prior conversation)"
        lines = []
        for turn in history:
            label = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{label}: {turn.content}")
        return "\n".join(lines)

    async def run(
        self,
        question: str,
        client_profile: ClientProfile,
        current_portfolio: Portfolio,
        conversation_history: list[ConversationTurn] | None = None,
    ) -> PortfolioQueryResponse:
        history = conversation_history or []

        # Step 1
        fund_view = self.fund_view_loader.load()
        print(f"Step 1: Loading fund view... ✓ ({len(fund_view.split())} words)")

        # Step 2
        formatted_history = self._format_history(history)
        print(f"Step 2: Context formatted ✓ ({len(history)} prior turns)")

        # Step 3
        print(f"Step 3: Calling portfolio_query skill...")
        data, usage = await self.query_skill.run(
            fund_view=fund_view,
            client_profile=client_profile.model_dump_json(indent=2),
            current_portfolio=current_portfolio.model_dump_json(indent=2),
            conversation_history=formatted_history,
            question=question,
            guardrail_rules=self._guardrail_rules,
        )
        print(
            f"  → Claude Haiku ✓ (tokens: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out)"
        )

        # Step 4
        response = PortfolioQueryResponse(
            answer=data.get("answer"),
            guardrail_triggered=bool(data.get("guardrail_triggered", False)),
            redirect_message=data.get("redirect_message"),
        )
        if response.guardrail_triggered:
            print(f"Step 4: Guardrail triggered → redirecting client")
        else:
            print(f"Step 4: Answer generated ✓")

        return response
