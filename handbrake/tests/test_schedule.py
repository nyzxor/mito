from __future__ import annotations

import pytest

from handbrake.schedule.cron import CronError, next_after, parse_cron
from handbrake.schedule.intervals import interval_s
from handbrake.schedule.scheduler import Scheduler

pytestmark = pytest.mark.handbrake


def test_aliases_and_step() -> None:
    daily = parse_cron("@daily")
    assert daily.minute == frozenset({0}) and daily.hour == frozenset({0})
    stepped = parse_cron("*/15 * * * *")
    assert 0 in stepped.minute and 15 in stepped.minute and 7 not in stepped.minute


def test_bad_cron_rejected() -> None:
    with pytest.raises(CronError):
        parse_cron("* * *")


def test_next_after_is_strictly_later() -> None:
    nxt = next_after("@hourly", 0)
    assert nxt >= 60


def test_overdue_jobs_coalesce_to_one_run() -> None:
    hits: list[int] = []
    sched = Scheduler()
    sched.add_interval("n", 60, lambda: hits.append(1), now=0)
    assert sched.fire(10) == []
    assert sched.fire(10_000) == ["n"]
    assert hits == [1]
    assert sched.fire(10_000) == []


def test_pulse_interval_scales() -> None:
    cfg = {"base_interval_s": 900.0, "frugal_multiplier": 2.0, "starving_multiplier": 4.0}
    assert interval_s("NORMAL", cfg) == 900
    assert interval_s("FRUGAL", cfg) == 1800
    assert interval_s("STARVING", cfg) == 3600
    assert interval_s("DEEP_REST", cfg) == 3600
