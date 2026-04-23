from __future__ import annotations

from datetime import datetime
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from .models import MacroSnapshot
from .prompts import DOCUMENT_GENERATION_PROMPT

_DOCUMENT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 3072  # 2 pages of Markdown; tuned below 4096 for cost/latency


def _fmt(value: Optional[float], precision: int = 2) -> str:
    """Format a nullable float for prompt injection. Returns 'N/A' if None."""
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}"


def _spread(a: Optional[float], b: Optional[float]) -> str:
    """Compute spread between two rates. Returns 'N/A' if either is missing."""
    if a is None or b is None:
        return "N/A"
    return f"{(a - b):.2f}"


def _build_prompt_vars(inputs: dict) -> dict:
    """Convert {"snapshot": MacroSnapshot, "date": datetime} to template variable dict."""
    snapshot: MacroSnapshot = inputs["snapshot"]
    date: datetime = inputs.get("date") or datetime.utcnow()

    edition = date.strftime("%B %Y")      # e.g. "March 2026"
    date_str = date.strftime("%d %B %Y")  # e.g. "12 March 2026"
    data_gaps_str = ", ".join(snapshot.data_gaps) if snapshot.data_gaps else "None"

    return {
        "date": date_str,
        "edition": edition,
        # Monetary policy
        "repo_rate_pct": _fmt(snapshot.repo_rate_pct),
        "rbi_stance": snapshot.rbi_stance or "N/A",
        "fed_funds_rate_pct": _fmt(snapshot.fed_funds_rate_pct),
        # Inflation
        "cpi_yoy_pct": _fmt(snapshot.cpi_yoy_pct),
        # Fixed income — with pre-computed spreads
        "gsec_10yr_yield_pct": _fmt(snapshot.gsec_10yr_yield_pct),
        "sbi_fd_1yr_rate_pct": _fmt(snapshot.sbi_fd_1yr_rate_pct),
        "gsec_repo_spread": _spread(snapshot.gsec_10yr_yield_pct, snapshot.repo_rate_pct),
        "gsec_fd_spread": _spread(snapshot.gsec_10yr_yield_pct, snapshot.sbi_fd_1yr_rate_pct),
        # Equity
        "nifty50_pe": _fmt(snapshot.nifty50_pe, precision=1),
        "nifty_midcap150_pe": _fmt(snapshot.nifty_midcap150_pe, precision=1),
        "nifty_smallcap250_pe": _fmt(snapshot.nifty_smallcap250_pe, precision=1),
        # Commodities
        "brent_crude_usd": _fmt(snapshot.brent_crude_usd, precision=1),
        "gold_price_inr_per_10g": _fmt(snapshot.gold_price_inr_per_10g, precision=0),
        "gold_price_usd_per_oz": _fmt(snapshot.gold_price_usd_per_oz, precision=0),
        # Flows & FX
        "fii_net_flows_cr_inr": _fmt(snapshot.fii_net_flows_cr_inr, precision=0),
        "usd_inr_rate": _fmt(snapshot.usd_inr_rate, precision=2),
        # Metadata
        "data_gaps": data_gaps_str,
    }


_llm = ChatAnthropic(model=_DOCUMENT_MODEL, max_tokens=_MAX_TOKENS)

document_generation_chain = (
    RunnableLambda(_build_prompt_vars)
    | DOCUMENT_GENERATION_PROMPT
    | _llm
    | StrOutputParser()
)


def generate_document(snapshot: MacroSnapshot, date: Optional[datetime] = None) -> str:
    """Generate a 2-page Markdown market commentary from a MacroSnapshot."""
    return document_generation_chain.invoke({"snapshot": snapshot, "date": date})


class DocumentGenerator:
    """Thin wrapper around document_generation_chain for backward compatibility."""

    def generate(self, snapshot: MacroSnapshot, date: Optional[datetime] = None) -> str:
        return generate_document(snapshot, date)
