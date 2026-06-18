"""SQLAlchemy ORM models — benchmarks domain.

Defines the benchmark-index catalogue + its daily EOD value rows. Imported by
services and Alembic migrations; avoid importing FastAPI or routers from here to
prevent circular dependencies.
"""

from app.domains.benchmarks.models.benchmark_index import BenchmarkIndex
from app.domains.benchmarks.models.benchmark_index_value import BenchmarkIndexValue

__all__ = [
    "BenchmarkIndex",
    "BenchmarkIndexValue",
]
