"""Loop types (guide impl-01 §1): harness-owned budget counters and the Turn envelope."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from mito.gateway.ir import Message


@dataclass
class Budget:
    max_steps: int = 16
    max_tool_calls: int = 32
    max_wall_seconds: float = 180.0
    max_write_actions: int = 3
    max_consecutive_failures: int = 3
    steps: int = 0
    tool_calls: int = 0
    writes: int = 0
    consecutive_failures: int = 0
    _deadline: float = 0.0

    def start(self) -> None:
        self._deadline = time.monotonic() + self.max_wall_seconds

    def extend_wall(self, seconds: float) -> None:
        self._deadline += seconds

    def stop_reason(self) -> str | None:
        if self.steps >= self.max_steps:
            return "max_steps"
        if self.tool_calls >= self.max_tool_calls:
            return "max_tool_calls"
        if time.monotonic() > self._deadline:
            return "timeout"
        if self.consecutive_failures >= self.max_consecutive_failures:
            return "failing"
        return None

    def teach(self) -> str:
        return (
            f"Budget: {self.max_steps} steps, {self.max_tool_calls} tool calls, "
            f"{self.max_write_actions} write actions, {int(self.max_wall_seconds)}s. "
            "If you are not converging by half, stop and report."
        )


@dataclass
class Turn:
    text: str
    # completed | max_steps | max_tool_calls | timeout | failing | budget_usd |
    # halted | brake_lost | error
    stop_reason: str
    steps: int
    cost_usd: float
    messages: list[Message]
    trace: list[dict[str, Any]] = field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)

    def tools_called(self) -> list[str]:
        return [str(e["name"]) for e in self.trace if e.get("ev") == "tool"]
