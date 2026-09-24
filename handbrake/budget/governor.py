"""Budget governor (DESIGN §5.5). Caps live here, in code; the prompt only *teaches* them.

Spend is recorded pessimistically at pre-flight (estimate) and replaced by the actual cost at
reconciliation. Windows are computed from persisted postings so a restart cannot reset them.
Phase 3 replaces `SpendStore` with the double-entry ATP ledger; the governor's interface stays.
"""

from __future__ import annotations

import sqlite3
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handbrake.canonical import canonical_json

CLOUD_TIERS = frozenset({"L2", "L3"})
FRONTIER_TIER = "L3"
HOUR = 3600.0
DAY = 86400.0
MONTH = 30 * DAY


class BudgetExceeded(Exception):
    def __init__(self, dimension: str, reason: str) -> None:
        super().__init__(reason)
        self.dimension = dimension
        self.reason = reason


@dataclass(frozen=True)
class BudgetLimits:
    per_call_max: float
    per_task: float
    per_hour: float
    per_day: float
    per_month_cloud: float
    frontier_calls_per_day: int

    @classmethod
    def from_metabolism(cls, path: Path) -> BudgetLimits:
        with path.open("rb") as f:
            data = tomllib.load(f)
        b = data["budgets_usd"]
        return cls(
            per_call_max=float(b["per_call_max"]),
            per_task=float(b["per_task"]),
            per_hour=float(b["per_hour"]),
            per_day=float(b["per_day"]),
            per_month_cloud=float(b["per_month_cloud"]),
            frontier_calls_per_day=int(b["frontier_calls_per_day"]),
        )


@dataclass(frozen=True)
class Grant:
    meter_id: str
    est_usd: float
    tier: str


class SpendStore:
    """SQLite-backed postings (WAL). One row per metered call."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS postings(
                 meter_id TEXT PRIMARY KEY, ts REAL NOT NULL, session TEXT NOT NULL,
                 task_path TEXT NOT NULL, tier TEXT NOT NULL, model_id TEXT NOT NULL,
                 est_usd REAL NOT NULL, actual_usd REAL, usage TEXT)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks(
                 task_id TEXT PRIMARY KEY, task_path TEXT NOT NULL, cap_usd REAL NOT NULL)"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS postings_ts ON postings(ts)")

    def add(
        self,
        meter_id: str,
        ts: float,
        session: str,
        task_path: str,
        tier: str,
        model_id: str,
        est: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO postings"
            "(meter_id, ts, session, task_path, tier, model_id, est_usd) "
            "VALUES(?,?,?,?,?,?,?)",
            (meter_id, ts, session, task_path, tier, model_id, est),
        )

    def reconcile(self, meter_id: str, actual: float, usage: dict[str, Any]) -> None:
        cur = self._conn.execute(
            "UPDATE postings SET actual_usd=?, usage=? WHERE meter_id=?",
            (actual, canonical_json(usage), meter_id),
        )
        if cur.rowcount != 1:
            raise KeyError(f"unknown meter id {meter_id}")

    def sum_since(self, since: float, *, cloud_only: bool) -> float:
        if cloud_only:
            q = (
                "SELECT COALESCE(SUM(COALESCE(actual_usd, est_usd)),0) FROM postings "
                "WHERE ts>=? AND tier IN ('L2','L3')"
            )
            row = self._conn.execute(q, (since,)).fetchone()
        else:
            q = "SELECT COALESCE(SUM(COALESCE(actual_usd, est_usd)),0) FROM postings WHERE ts>=?"
            row = self._conn.execute(q, (since,)).fetchone()
        return float(row[0])

    def count_tier_since(self, tier: str, since: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM postings WHERE ts>=? AND tier=?", (since, tier)
        ).fetchone()
        return int(row[0])

    def sum_task_path(self, task_path: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(COALESCE(actual_usd, est_usd)),0) "
            "FROM postings WHERE task_path=? OR task_path LIKE ?",
            (task_path, task_path + "/%"),
        ).fetchone()
        return float(row[0])

    def get_task(self, task_id: str) -> tuple[str, float] | None:
        row = self._conn.execute(
            "SELECT task_path, cap_usd FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        return (str(row[0]), float(row[1])) if row else None

    def put_task(self, task_id: str, task_path: str, cap: float) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks(task_id, task_path, cap_usd) VALUES(?,?,?)",
            (task_id, task_path, cap),
        )


class BudgetGovernor:
    def __init__(
        self,
        store: SpendStore,
        limits: BudgetLimits,
        *,
        clock: Callable[[], float] = time.time,
        breaker_threshold: int = 3,
        breaker_base_s: float = 30.0,
        breaker_max_s: float = 3600.0,
    ) -> None:
        self.store = store
        self.limits = limits
        self._clock = clock
        self._breaker_threshold = breaker_threshold
        self._breaker_base = breaker_base_s
        self._breaker_max = breaker_max_s
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._fingerprints: dict[tuple[str, str], int] = {}

    # ---- tasks -------------------------------------------------------------------------------
    def _task(self, task_id: str) -> tuple[str, float]:
        found = self.store.get_task(task_id)
        if found is None:
            self.store.put_task(task_id, task_id, self.limits.per_task)
            return task_id, self.limits.per_task
        return found

    def task_cap(self, task_id: str) -> float:
        return self._task(task_id)[1]

    def task_spent(self, task_id: str) -> float:
        path, _ = self._task(task_id)
        return self.store.sum_task_path(path)

    def carve(self, parent_task: str, child_task: str, max_usd: float) -> float:
        """Give a subagent its own cap, never more than the parent's remaining budget."""
        parent_path, parent_cap = self._task(parent_task)
        remaining = max(0.0, parent_cap - self.store.sum_task_path(parent_path))
        cap = min(max_usd, remaining)
        self.store.put_task(child_task, f"{parent_path}/{child_task}", cap)
        return cap

    # ---- windows -----------------------------------------------------------------------------
    def window_spent(self, seconds: float, *, cloud_only: bool) -> float:
        return self.store.sum_since(self._clock() - seconds, cloud_only=cloud_only)

    # ---- pre-flight / reconcile --------------------------------------------------------------
    def preflight(
        self, session: str, task_id: str, tier: str, est_usd: float, model_id: str
    ) -> Grant:
        lim = self.limits
        if est_usd > lim.per_call_max:
            raise BudgetExceeded(
                "per_call", f"estimated ${est_usd:.4f} exceeds per-call cap ${lim.per_call_max:.4f}"
            )
        if self.is_open(f"model:{model_id}"):
            raise BudgetExceeded("circuit_open", f"circuit breaker open for model {model_id}")
        path, cap = self._task(task_id)
        spent = self.store.sum_task_path(path)
        if spent + est_usd > cap:
            raise BudgetExceeded(
                "per_task", f"task {task_id} would exceed cap ${cap:.4f} (spent ${spent:.4f})"
            )
        for parent in _ancestors(path):
            p = self.store.get_task(parent)
            if p and self.store.sum_task_path(p[0]) + est_usd > p[1]:
                raise BudgetExceeded("per_task", f"parent task {parent} would exceed its cap")
        now = self._clock()
        if tier in CLOUD_TIERS:
            # Window caps bound real money (cloud). Local burn is metered and shows up in the
            # ATP runway (Phase 3), but must not starve the local tier by itself.
            if self.store.sum_since(now - HOUR, cloud_only=True) + est_usd > lim.per_hour:
                raise BudgetExceeded("per_hour", f"hourly cloud cap ${lim.per_hour:.2f} reached")
            if self.store.sum_since(now - DAY, cloud_only=True) + est_usd > lim.per_day:
                raise BudgetExceeded("per_day", f"daily cloud cap ${lim.per_day:.2f} reached")
            if self.store.sum_since(now - MONTH, cloud_only=True) + est_usd > lim.per_month_cloud:
                raise BudgetExceeded(
                    "per_month_cloud", f"monthly cloud cap ${lim.per_month_cloud:.2f} reached"
                )
        if (
            tier == FRONTIER_TIER
            and self.store.count_tier_since(FRONTIER_TIER, now - DAY) >= lim.frontier_calls_per_day
        ):
            raise BudgetExceeded(
                "frontier_per_day", f"{lim.frontier_calls_per_day} frontier calls/day reached"
            )
        meter_id = uuid.uuid4().hex
        self.store.add(meter_id, now, session, path, tier, model_id, est_usd)
        return Grant(meter_id, est_usd, tier)

    def reconcile(self, meter_id: str, actual_usd: float, usage: dict[str, Any]) -> None:
        self.store.reconcile(meter_id, actual_usd, usage)

    # ---- no-progress detector ----------------------------------------------------------------
    def check_progress(self, session: str, fingerprint: str) -> str:
        key = (session, fingerprint)
        n = self._fingerprints.get(key, 0) + 1
        self._fingerprints[key] = n
        if n >= 3:
            return "stop"
        return "warn" if n == 2 else "ok"

    def reset_progress(self, session: str) -> None:
        for key in [k for k in self._fingerprints if k[0] == session]:
            del self._fingerprints[key]

    # ---- circuit breakers --------------------------------------------------------------------
    def record_failure(self, key: str) -> None:
        n = self._failures.get(key, 0) + 1
        self._failures[key] = n
        if n >= self._breaker_threshold:
            backoff = min(
                self._breaker_base * 2 ** (n - self._breaker_threshold), self._breaker_max
            )
            self._open_until[key] = self._clock() + backoff

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._open_until.pop(key, None)

    def is_open(self, key: str) -> bool:
        until = self._open_until.get(key)
        return until is not None and self._clock() < until


def _ancestors(task_path: str) -> list[str]:
    parts = task_path.split("/")
    return parts[:-1]
