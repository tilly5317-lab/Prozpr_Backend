"""The net-worth job must report a run outcome and its per-user counts.

It is the only scheduled job that touches every user's money, and it was the
only one with no tracing at all. Its per-user handler ("never let one user block
the rest") means 40 of 50 users can fail while the run reports success.
"""

from app.core import job_tracing
from app.domains.portfolio.services import networth_history_service as svc


def test_the_daily_job_is_traced():
    """@traced_job wraps with functools.wraps, so the name survives and a
    __wrapped__ attribute appears."""
    assert hasattr(svc.run_daily_networth_job, "__wrapped__"), (
        "run_daily_networth_job is not decorated with @traced_job"
    )


def test_the_job_records_its_per_user_counts():
    """A run that 'succeeds' having refreshed nobody is broken. The counts are
    what make that visible, so the call must survive refactors of the loop."""
    import inspect

    source = inspect.getsource(svc.run_daily_networth_job.__wrapped__)
    assert "record_job_counts" in source
    for field in ("users_total", "users_refreshed", "users_failed"):
        assert field in source, f"{field} missing from the job's recorded counts"


async def test_counts_reach_job_completed(monkeypatch):
    """End-to-end through the real decorator: what a job records is what the
    event carries."""
    sent: list[dict] = []
    monkeypatch.setattr(job_tracing, "capture_job_completed", lambda **kw: sent.append(kw))

    @job_tracing.traced_job("networth.daily_job")
    async def fake_job():
        job_tracing.record_job_counts(users_total=50, users_refreshed=10, users_failed=40)

    await fake_job()
    assert sent[0]["job"] == "networth.daily_job"
    assert sent[0]["counts"]["users_failed"] == 40
