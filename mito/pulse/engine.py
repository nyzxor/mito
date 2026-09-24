"""Pulse decision (DESIGN §4.3). L0 only: no model call lives in this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from handbrake.schedule.intervals import interval_s

MAX_SIGNALS = 5


@dataclass(frozen=True)
class Signal:
    kind: str
    key: str


@dataclass(frozen=True)
class PulseDecision:
    action: str  # skip | rest | turn
    reason: str
    interval_s: float


@dataclass
class Pulse:
    pulse_cfg: dict[str, float]
    seen: dict[str, float] = field(default_factory=dict)

    def decide(self, *, state: str, signals: list[Signal], now: float) -> PulseDecision:
        gap = interval_s(state, self.pulse_cfg)
        if state == "DEEP_REST":
            return PulseDecision("rest", "deep rest: wake-watcher only, no model", gap)
        fresh: list[Signal] = []
        for sig in signals:
            last = self.seen.get(sig.key)
            if last is not None and now - last < gap:
                continue
            fresh.append(sig)
            if len(fresh) >= MAX_SIGNALS:
                break
        if not fresh:
            return PulseDecision("skip", "no signal", gap)
        for sig in fresh:
            self.seen[sig.key] = now
        kinds = ",".join(s.kind for s in fresh)
        return PulseDecision("turn", kinds, gap)


def signals_from_state(state: dict[str, Any]) -> list[Signal]:
    out: list[Signal] = []
    pending = int(state.get("pending_approvals") or 0)
    if pending:
        out.append(Signal("approvals", f"approvals:{pending}"))
    unseen = int(state.get("mail_unseen") or 0)
    for i in range(unseen):
        out.append(Signal("mail", f"mail:{i}"))
    return out
