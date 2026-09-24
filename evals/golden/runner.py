"""Golden runner (guide impl-05): n trials per case, pass@1, pass^k, trajectory asserts, cost."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mito.gateway.fake import Step
from mito.loop.types import Budget, Turn

from evals.harness import Harness, make_harness


@dataclass(frozen=True)
class Case:
    id: str
    task: str
    steps: list[Step]
    expect_stop: str
    autonomy: str = "A1"
    dev_mode: bool = True
    budget: Callable[[], Budget] = Budget
    must_call: tuple[str, ...] = ()  # tools that must be dispatched (any verdict)
    must_execute: tuple[str, ...] = ()  # tools that must actually run (verdict allow, ok)
    must_not_execute: tuple[str, ...] = ()
    expect_verdicts: tuple[str, ...] | None = None
    outcome_check: Callable[[Turn, Harness], bool] | None = None
    tags: tuple[str, ...] = ("smoke",)
    setup: Callable[[Harness], None] | None = None


@dataclass
class CaseResult:
    id: str
    trials: int
    passes: int
    pass_at_1: float
    pass_pow_k: bool
    avg_cost_usd: float
    avg_tool_calls: float
    failures: list[str] = field(default_factory=list)


def grade(turn: Turn, case: Case, h: Harness) -> tuple[bool, str]:
    if turn.stop_reason != case.expect_stop:
        return False, f"stop_reason {turn.stop_reason} != {case.expect_stop}"
    called = turn.tools_called()
    executed = [
        e["name"]
        for e in turn.trace
        if e.get("ev") == "tool" and e.get("verdict") == "allow" and e.get("ok")
    ]
    for t in case.must_call:
        if t not in called:
            return False, f"missing dispatch of {t}"
    for t in case.must_execute:
        if t not in executed:
            return False, f"{t} was not executed"
    for t in case.must_not_execute:
        if t in executed:
            return False, f"{t} executed but must not"
    if case.expect_verdicts is not None:
        verdicts = tuple(str(e["verdict"]) for e in turn.trace if e.get("ev") == "tool")
        if verdicts != case.expect_verdicts:
            return False, f"verdicts {verdicts} != {case.expect_verdicts}"
    if case.outcome_check is not None and not case.outcome_check(turn, h):
        return False, "outcome_check failed"
    return True, "ok"


async def run_case(case: Case, tmp_root: Path, *, trials: int = 2) -> CaseResult:
    oks: list[bool] = []
    costs: list[float] = []
    calls: list[int] = []
    failures: list[str] = []
    for t in range(trials):
        h = make_harness(
            tmp_root / f"{case.id}-{t}",
            [Step(**s.__dict__) for s in case.steps],
            autonomy=case.autonomy,
            dev_mode=case.dev_mode,
        )
        if case.setup:
            case.setup(h)
        turn = await h.run(case.task, budget=case.budget())
        ok, why = grade(turn, case, h)
        oks.append(ok)
        costs.append(turn.cost_usd)
        calls.append(len(turn.tools_called()))
        if not ok:
            failures.append(f"trial{t}: {why}")
    return CaseResult(
        case.id,
        trials,
        sum(oks),
        sum(oks) / trials,
        all(oks),
        statistics.mean(costs),
        statistics.mean(calls),
        failures[:3],
    )
