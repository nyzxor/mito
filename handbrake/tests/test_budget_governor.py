"""DESIGN §5.5: caps per call/task/hour/day/month enforced in code; pre-flight estimate,
post-call reconciliation, no-progress detector, circuit breakers, per-subagent carve-outs."""

from __future__ import annotations

from pathlib import Path

import pytest

from handbrake.budget.governor import (
    BudgetExceeded,
    BudgetGovernor,
    BudgetLimits,
    SpendStore,
)

pytestmark = pytest.mark.handbrake

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


LIMITS = BudgetLimits(
    per_call_max=0.05,
    per_task=0.25,
    per_hour=0.25,
    per_day=1.00,
    per_month_cloud=15.00,
    frontier_calls_per_day=5,
)


@pytest.fixture
def gov(tmp_path: Path) -> tuple[BudgetGovernor, Clock]:
    clock = Clock()
    store = SpendStore(tmp_path / "hb.sqlite")
    return BudgetGovernor(store, LIMITS, clock=clock), clock


def test_limits_load_from_metabolism_toml() -> None:
    limits = BudgetLimits.from_metabolism(ROOT / "config" / "metabolism.toml")
    assert limits.per_task == 0.25 and limits.per_day == 1.0 and limits.frontier_calls_per_day == 5


def test_preflight_grants_and_reconciles(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, _ = gov
    grant = g.preflight("s", "t1", "L2", 0.01, "m")
    assert grant.meter_id
    assert g.task_spent("t1") == pytest.approx(0.01)  # pessimistic until reconciled
    g.reconcile(grant.meter_id, 0.004, {"prompt_tokens": 100, "completion_tokens": 20})
    assert g.task_spent("t1") == pytest.approx(0.004)


def test_per_call_cap(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, _ = gov
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "t", "L2", 0.06, "m")
    assert ei.value.dimension == "per_call"


def test_per_task_cap(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, _ = gov
    for _ in range(5):
        g.preflight("s", "t", "L2", 0.05, "m")
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "t", "L2", 0.01, "m")
    assert ei.value.dimension == "per_task"


def test_hourly_window_resets(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, clock = gov
    for i in range(5):
        g.preflight("s", f"t{i}", "L2", 0.05, "m")
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "t9", "L2", 0.01, "m")
    assert ei.value.dimension == "per_hour"
    clock.advance(3601)
    g.preflight("s", "t9", "L2", 0.01, "m")


def test_window_caps_bound_cloud_only_and_local_is_still_metered(
    gov: tuple[BudgetGovernor, Clock],
) -> None:
    g, clock = gov
    # local (L1) spend is metered (shows in runway) but never trips the cloud window caps
    for i in range(30):
        g.preflight("s", f"l{i}", "L1", 0.05, "local")
        clock.advance(3600)
    assert g.window_spent(30 * 86400, cloud_only=True) == 0.0
    assert g.window_spent(30 * 86400, cloud_only=False) == pytest.approx(1.5)


def test_daily_cloud_cap(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, clock = gov
    for i in range(20):
        g.preflight("s", f"d{i}", "L2", 0.05, "m")
        clock.advance(3600)  # spread so the hourly cap never trips
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "d99", "L2", 0.01, "m")
    assert ei.value.dimension == "per_day"


def test_frontier_calls_per_day(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, clock = gov
    for i in range(5):
        g.preflight("s", f"f{i}", "L3", 0.01, "big")
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "f9", "L3", 0.01, "big")
    assert ei.value.dimension == "frontier_per_day"
    clock.advance(86401)
    g.preflight("s", "f9", "L3", 0.01, "big")


def test_subagent_budget_is_carved_from_parent(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, _ = gov
    g.preflight("s", "parent", "L2", 0.05, "m")
    g.carve("parent", "child", 0.10)
    g.preflight("s", "child", "L2", 0.05, "m")
    g.preflight("s", "child", "L2", 0.05, "m")
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "child", "L2", 0.01, "m")
    assert ei.value.dimension == "per_task" and "child" in ei.value.reason
    # child spend counted against the parent too
    assert g.task_spent("parent") == pytest.approx(0.15)


def test_carve_cannot_exceed_parent_remaining(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, _ = gov
    g.preflight("s", "p", "L2", 0.05, "m")
    g.carve("p", "c", 0.50)
    assert g.task_cap("c") == pytest.approx(0.20)


def test_no_progress_detector(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, _ = gov
    assert g.check_progress("s", "fp1") == "ok"
    assert g.check_progress("s", "fp1") == "warn"
    assert g.check_progress("s", "fp1") == "stop"
    assert g.check_progress("s", "fp2") == "ok"
    assert g.check_progress("other", "fp1") == "ok"


def test_circuit_breaker_opens_and_backs_off(gov: tuple[BudgetGovernor, Clock]) -> None:
    g, clock = gov
    for _ in range(3):
        g.record_failure("model:m")
    assert g.is_open("model:m")
    with pytest.raises(BudgetExceeded) as ei:
        g.preflight("s", "t", "L2", 0.01, "m")
    assert ei.value.dimension == "circuit_open"
    clock.advance(31)
    assert not g.is_open("model:m")
    g.record_failure("model:m")  # 4th consecutive -> longer backoff
    assert g.is_open("model:m")
    clock.advance(31)
    assert g.is_open("model:m")
    clock.advance(60)
    assert not g.is_open("model:m")
    g.record_success("model:m")
    g.record_failure("model:m")
    assert not g.is_open("model:m")


def test_spend_persists_across_reopen(tmp_path: Path) -> None:
    clock = Clock()
    g1 = BudgetGovernor(SpendStore(tmp_path / "hb.sqlite"), LIMITS, clock=clock)
    g1.preflight("s", "t", "L2", 0.05, "m")
    g2 = BudgetGovernor(SpendStore(tmp_path / "hb.sqlite"), LIMITS, clock=clock)
    assert g2.task_spent("t") == pytest.approx(0.05)
