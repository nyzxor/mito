"""In-process scheduler (ADR-0017). Overdue jobs fire once (coalesced), not N times."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from handbrake.schedule.cron import next_after

JobFn = Callable[[], None]


@dataclass
class _Job:
    name: str
    every_s: float | None
    cron: str | None
    fn: JobFn
    next_at: float


@dataclass
class Scheduler:
    jobs: dict[str, _Job] = field(default_factory=dict)
    max_jobs: int = 32

    def add_interval(self, name: str, every_s: float, fn: JobFn, *, now: float) -> None:
        if every_s < 60:
            raise ValueError(f"interval for {name} is {every_s}s; minimum is 60s")
        self._put(name, _Job(name, every_s, None, fn, now + every_s))

    def add_cron(self, name: str, expr: str, fn: JobFn, *, now: float) -> None:
        self._put(name, _Job(name, None, expr, fn, next_after(expr, now)))

    def _put(self, name: str, job: _Job) -> None:
        if name not in self.jobs and len(self.jobs) >= self.max_jobs:
            raise ValueError(f"job cap {self.max_jobs} reached")
        self.jobs[name] = job

    def fire(self, now: float) -> list[str]:
        ran: list[str] = []
        for job in list(self.jobs.values()):
            if now < job.next_at:
                continue
            job.fn()
            ran.append(job.name)
            if job.every_s is not None:
                job.next_at = now + job.every_s
            elif job.cron is not None:
                job.next_at = next_after(job.cron, now)
        return ran
