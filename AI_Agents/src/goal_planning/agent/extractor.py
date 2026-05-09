"""NL extractor — Phase 3 implementation. Stub here for Phase 2 tool wiring."""
from __future__ import annotations
from datetime import date

from goal_planning.models import (
    ExtractedFinancialEvent, ExtractionError,
)


class FinancialEventExtractor:
    def __init__(self, model: str | None = None):
        self._model = model

    async def extract(
        self,
        description: str,
        anchor_date: date,
        existing_goal_names: list[str],
    ) -> ExtractedFinancialEvent | ExtractionError:
        # Stub for Phase 2 tests; real impl in Phase 3
        return ExtractionError(kind="error", reason="Extractor not yet implemented (Phase 3)")
