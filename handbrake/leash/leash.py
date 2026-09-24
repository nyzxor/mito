"""Leash / dead-man's switch (DESIGN §5.2). Autonomy decays without operator check-in:
first TTL -> demote one level; second TTL -> Deep Rest. Unsupervised means more restricted."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from handbrake.policy.autonomy import AutonomyStore


@dataclass(frozen=True)
class LeashStatus:
    last_checkin: float
    last_source: str
    ttl_s: float
    expired_periods: int
    remaining_s: float
    applied_periods: int


class Leash:
    def __init__(
        self, path: Path, *, ttl_hours: float = 72.0, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = path
        self.ttl_s = ttl_hours * 3600.0
        self._clock = clock
        if not self.path.exists():
            self._write(self._clock(), "init", 0)

    def _read(self) -> tuple[float, str, int]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return (
                float(d["last_checkin"]),
                str(d.get("source", "?")),
                int(d.get("applied_periods", 0)),
            )
        except (ValueError, KeyError, OSError):
            return 0.0, "corrupt", 0  # unreadable leash reads as long expired (fail closed)

    def _write(self, ts: float, source: str, applied: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"last_checkin": ts, "source": source, "applied_periods": applied}),
            encoding="utf-8",
        )

    def checkin(self, source: str) -> None:
        self._write(self._clock(), source, 0)

    def status(self) -> LeashStatus:
        last, source, applied = self._read()
        elapsed = self._clock() - last
        expired = int(elapsed // self.ttl_s) if elapsed >= self.ttl_s else 0
        remaining = max(0.0, self.ttl_s - elapsed)
        return LeashStatus(last, source, self.ttl_s, expired, remaining, applied)

    def apply(self, autonomy: AutonomyStore, *, rest: Callable[[], object]) -> list[str]:
        """Idempotent: applies only the consequences not yet applied for the current expiry."""
        st = self.status()
        actions: list[str] = []
        if st.expired_periods >= 1 and st.applied_periods < 1:
            actions.append(f"demote:{autonomy.demote('leash expired')}")
        if st.expired_periods >= 2 and st.applied_periods < 2:
            rest()
            actions.append("deep_rest")
        if actions:
            self._write(st.last_checkin, st.last_source, min(st.expired_periods, 2))
        return actions
