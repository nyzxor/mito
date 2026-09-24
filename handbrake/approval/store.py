"""Approval gate storage (DESIGN §5.8). Cards are bound to the exact action hash; a decision
must come from an operator channel; timeout = deny; approvals are single use."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handbrake.canonical import action_hash

DEFAULT_TTL_S = 24 * 3600.0


@dataclass(frozen=True)
class ApprovalCard:
    action_hash: str
    session: str
    tool: str
    args: dict[str, Any]
    taint: list[str]
    reason: str
    tier: str
    est_cost_atp: float
    purpose: str
    created: float
    expires: float
    status: str  # pending | approved | denied | expired | consumed

    def render(self) -> str:
        left = max(0, int(self.expires - time.time()))
        lines = [
            f"[{self.action_hash[:12]}] {self.tool}  "
            f"tier={self.tier}  cost≈{self.est_cost_atp:.0f} ATP",
            f"  why: {self.purpose or '-'}",
            f"  gate: {self.reason}",
            f"  args: {json.dumps(self.args, ensure_ascii=False)[:400]}",
            f"  taint: {'+'.join(self.taint) or '-'}   expires in {left}s",
        ]
        return "\n".join(lines)


class ApprovalStore:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        default_ttl_s: float = DEFAULT_TTL_S,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS approvals(
                 action_hash TEXT PRIMARY KEY, session TEXT, tool TEXT, args TEXT, taint TEXT,
                 reason TEXT, tier TEXT, est_cost_atp REAL, purpose TEXT,
                 created REAL, expires REAL, status TEXT, decided_by TEXT)"""
        )
        self._clock = clock
        self._ttl = default_ttl_s

    def request(
        self,
        session: str,
        tool: str,
        args: dict[str, Any],
        taint: list[str],
        *,
        reason: str,
        tier: str,
        est_cost_atp: float,
        purpose: str,
        ttl_s: float | None = None,
    ) -> ApprovalCard:
        h = action_hash(tool, args, session, taint)
        existing = self.get(h)
        if existing is not None:
            return existing
        now = self._clock()
        card = ApprovalCard(
            h,
            session,
            tool,
            args,
            sorted(taint),
            reason,
            tier,
            est_cost_atp,
            purpose,
            now,
            now + (ttl_s or self._ttl),
            "pending",
        )
        self._conn.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                h,
                session,
                tool,
                json.dumps(args, sort_keys=True),
                json.dumps(card.taint),
                reason,
                tier,
                est_cost_atp,
                purpose,
                now,
                card.expires,
                "pending",
            ),
        )
        return card

    def get(self, h: str) -> ApprovalCard | None:
        row = self._conn.execute(
            """SELECT action_hash, session, tool, args, taint, reason, tier,
                      est_cost_atp, purpose, created, expires, status
               FROM approvals WHERE action_hash=?""",
            (h,),
        ).fetchone()
        if row is None:
            return None
        card = ApprovalCard(
            row[0],
            row[1],
            row[2],
            json.loads(row[3]),
            json.loads(row[4]),
            row[5],
            row[6],
            float(row[7]),
            row[8],
            float(row[9]),
            float(row[10]),
            row[11],
        )
        if card.status == "pending" and self._clock() > card.expires:
            self._conn.execute(
                "UPDATE approvals SET status='expired' WHERE action_hash=? AND status='pending'",
                (h,),
            )
            return ApprovalCard(**{**card.__dict__, "status": "expired"})
        return card

    def status(self, h: str) -> str:
        card = self.get(h)
        return "unknown" if card is None else card.status

    def pending(self) -> list[ApprovalCard]:
        rows = self._conn.execute(
            "SELECT action_hash FROM approvals WHERE status='pending' ORDER BY created"
        ).fetchall()
        cards = [self.get(str(r[0])) for r in rows]
        return [c for c in cards if c is not None and c.status == "pending"]

    def decide(self, h: str, *, approve: bool, by: str) -> str:
        card = self.get(h)
        if card is None or card.status != "pending":
            return "unknown" if card is None else card.status
        status = "approved" if approve else "denied"
        self._conn.execute(
            "UPDATE approvals SET status=?, decided_by=? WHERE action_hash=?", (status, by, h)
        )
        return status

    def consume(self, h: str) -> bool:
        """Mark an approved action as used exactly once. Returns False unless it was approved."""
        cur = self._conn.execute(
            "UPDATE approvals SET status='consumed' "
            "WHERE action_hash=? AND status='approved' AND expires>=?",
            (h, self._clock()),
        )
        return cur.rowcount == 1
