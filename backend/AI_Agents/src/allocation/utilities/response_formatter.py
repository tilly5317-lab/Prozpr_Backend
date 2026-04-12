from ..schemas.allocation import IdealAllocation
from ..schemas.portfolio import Portfolio
from ..schemas.delta import Delta
from ..schemas.recommendation import Recommendation
from ..schemas.allocation_response import AllocationResponse


class ResponseFormatter:
    def build(
        self,
        ideal_allocation: IdealAllocation,
        current_portfolio: Portfolio | None,
        delta: Delta | None,
        recommendation: Recommendation,
    ) -> AllocationResponse:
        return AllocationResponse(
            recommended_allocation=ideal_allocation,
            current_allocation=current_portfolio,
            delta=delta,
            narrative=recommendation.narrative,
            action_items=recommendation.action_items,
            confidence=recommendation.confidence,
            disclaimers=recommendation.disclaimers,
        )
