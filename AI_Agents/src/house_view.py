"""Structure-aware slicer for the hand-maintained fund-house view.

Advice paths (portfolio_query, rebalancing) take the Prozpr-only slice; market
takes the whole file. The Prozpr slice is allow-list: it emits ONLY `Prozpr view:`
paragraphs (under their section/question headers), so a house block — which never
starts with `Prozpr view:` — can never be emitted, and nested `####` factual
sub-questions with no Prozpr lead contribute nothing. A malformed file fails
closed (returns None): we never guess and never leak another house into advice.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from common import read_text_bom_aware  # BOM-safe: the .md may carry a UTF-8/UTF-16 BOM

logger = logging.getLogger(__name__)

_VIEW_PATH = Path(__file__).resolve().parents[1] / "Reference_docs" / "fund_house_commentry.md"

_CANONICAL_SECTIONS = {"Equities", "Bonds", "Commodities"}
_SECTION_RE = re.compile(r"^## (.+?)\s*$")
_QUESTION_RE = re.compile(r"^#{3,4} ")                  # ### question or #### sub-question
_ATTRIBUTION_RE = re.compile(r"^\*\*.+\(.+\):\*\*")     # **ICICI Prudential (Aug 2026):**
_PROZPR_LEAD_RE = re.compile(r"^prozpr view:", re.IGNORECASE)


def validate_house_view(text: str) -> list[str]:
    """Return structure errors; empty = valid. Deliberately minimal: the Prozpr-only
    slice is allow-list (it emits only `Prozpr view:` paragraphs), so no-leak is
    structural and the validator need not police attribution formatting. The one
    check that matters is canonical section names — what the loader fail-closes on
    and what asset-class keying will later need."""
    errors: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        m = _SECTION_RE.match(line)
        if m and m.group(1) not in _CANONICAL_SECTIONS:
            errors.append(f"L{n}: unknown section '{m.group(1)}'")
    return errors


def _prozpr_slice(text: str) -> str:
    """Allow-list: emit only `Prozpr view:` paragraph(s), under their section/question
    headers (emitted lazily, so a section/question with no view produces nothing)."""
    out: list[str] = []
    section: str | None = None
    question: str | None = None
    section_emitted = False
    capturing = False
    for line in text.splitlines():
        if _SECTION_RE.match(line):
            section, section_emitted, question, capturing = line, False, None, False
        elif _QUESTION_RE.match(line):
            question, capturing = line, False
        elif _ATTRIBUTION_RE.match(line):
            capturing = False                    # a house block ends the Prozpr view
        elif _PROZPR_LEAD_RE.match(line):
            if section and not section_emitted:
                out.append(section)
                section_emitted = True
            if question:
                out.append(question)
                question = None
            out.append(line)
            capturing = True
        elif capturing:
            out.append(line)                     # continuation line of the Prozpr view
    return "\n".join(out).strip()


def load_house_view(*, prozpr_only: bool) -> str | None:
    """The fund-house view: Prozpr-only slice for advice paths, whole file for market.
    Returns None when the file is missing / empty / fails validation (fail-closed)."""
    if not _VIEW_PATH.exists():
        logger.info("Fund-house view absent at %s; continuing without it", _VIEW_PATH)
        return None
    text = read_text_bom_aware(_VIEW_PATH).strip()
    if not text:
        return None
    errors = validate_house_view(text)
    if errors:
        logger.warning(
            "Fund-house view failed validation (%d issue/s); serving nothing: %s",
            len(errors), errors[:3],
        )
        return None
    return _prozpr_slice(text) if prozpr_only else text


__all__ = ["load_house_view", "validate_house_view"]
