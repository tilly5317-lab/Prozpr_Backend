"""Financial primitives — pure-Python, zero LLM, reusable across modules."""
from .time_value import future_value, present_value, compound
from .annuity import pmt, rate, ipmt, RATEConvergenceError
from .inflation import inflate, real_rate
from .dates import fy_for_date, fy_end_after, eomonth, year_fraction
from .retirement import retirement_corpus_pv

__all__ = [
    "future_value", "present_value", "compound",
    "pmt", "rate", "ipmt", "RATEConvergenceError",
    "inflate", "real_rate",
    "fy_for_date", "fy_end_after", "eomonth", "year_fraction",
    "retirement_corpus_pv",
]
