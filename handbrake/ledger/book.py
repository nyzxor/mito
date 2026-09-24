"""Double-entry ATP ledger (DESIGN §6, ADR-0003). Balance is derived, never a mutable counter.

Income claims do not move energy until `mito ledger confirm`. Metrics read verified income only.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENERGY = "energy:balance"
EXPENSES_LLM = "expenses:llm"
EXPENSES_TOOLS = "expenses:tools"
EXPENSES_INFRA = "expenses:infra"
EQUITY_TOPUPS = "equity:operator_topups"

ACCOUNTS = frozenset(
    {ENERGY, EXPENSES_LLM, EXPENSES_TOOLS, EXPENSES_INFRA, EQUITY_TOPUPS}
)


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    id: str
    ts: float
    amount_atp: float
    playbook: str
    evidence: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "amount_atp": self.amount_atp,
            "playbook": self.playbook,
            "evidence": self.evidence,
            "status": self.status,
        }


@dataclass(frozen=True)
class Journal:
    id: str
    ts: float
    kind: str
    memo: str


class Ledger:
    def __init__(self, path: Path, *, clock: Callable[[], float]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS journal(
                 id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,
                 memo TEXT NOT NULL, audit_seq INTEGER, claim_id TEXT)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS lines(
                 journal_id TEXT NOT NULL, account TEXT NOT NULL,
                 debit_atp REAL NOT NULL DEFAULT 0, credit_atp REAL NOT NULL DEFAULT 0)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS claims(
                 id TEXT PRIMARY KEY, ts REAL NOT NULL, amount_atp REAL NOT NULL,
                 playbook TEXT NOT NULL, evidence TEXT NOT NULL, status TEXT NOT NULL)"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS lines_acct ON lines(account)")

    def post(
        self,
        kind: str,
        memo: str,
        pairs: list[tuple[str, float, float]],
        *,
        audit_seq: int | None = None,
        claim_id: str | None = None,
    ) -> str:
        debits = sum(d for _a, d, _c in pairs)
        credits = sum(c for _a, _d, c in pairs)
        if abs(debits - credits) > 1e-9 or debits <= 0:
            raise LedgerError(f"unbalanced journal: debit={debits} credit={credits}")
        for account, debit, credit in pairs:
            if account not in ACCOUNTS and not account.startswith("income:"):
                raise LedgerError(f"unknown account {account}")
            if (debit > 0 and credit > 0) or (debit < 0 or credit < 0):
                raise LedgerError("each line is a single-sided non-negative amount")
        jid = uuid.uuid4().hex[:12]
        ts = self._clock()
        self._conn.execute(
            "INSERT INTO journal(id, ts, kind, memo, audit_seq, claim_id) VALUES(?,?,?,?,?,?)",
            (jid, ts, kind, memo, audit_seq, claim_id),
        )
        for account, debit, credit in pairs:
            self._conn.execute(
                "INSERT INTO lines(journal_id, account, debit_atp, credit_atp) VALUES(?,?,?,?)",
                (jid, account, debit, credit),
            )
        return jid

    def balance_atp(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(debit_atp)-SUM(credit_atp),0) FROM lines WHERE account=?",
            (ENERGY,),
        ).fetchone()
        return float(row[0])

    def has_endowment(self) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM journal WHERE kind IN ('topup','income.verify')"
        ).fetchone()
        return int(row[0]) > 0

    def expense(self, atp: float, *, account: str, memo: str) -> str | None:
        if atp <= 0:
            return None
        return self.post(
            "expense",
            memo,
            [(account, atp, 0.0), (ENERGY, 0.0, atp)],
        )

    def topup(self, atp: float, *, memo: str = "operator top-up") -> str:
        if atp <= 0:
            raise LedgerError("top-up must be positive")
        return self.post("topup", memo, [(ENERGY, atp, 0.0), (EQUITY_TOPUPS, 0.0, atp)])

    def file_claim(self, amount_atp: float, playbook: str, evidence: str) -> Claim:
        if amount_atp <= 0:
            raise LedgerError("claim amount must be positive")
        cid = uuid.uuid4().hex[:12]
        ts = self._clock()
        self._conn.execute(
            "INSERT INTO claims(id, ts, amount_atp, playbook, evidence, status) "
            "VALUES(?,?,?,?,?,?)",
            (cid, ts, amount_atp, playbook, evidence, "pending"),
        )
        return Claim(cid, ts, amount_atp, playbook, evidence, "pending")

    def confirm_claim(self, claim_id: str) -> Claim:
        row = self._conn.execute(
            "SELECT ts, amount_atp, playbook, evidence, status FROM claims WHERE id=?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown claim {claim_id}")
        if row[4] != "pending":
            raise LedgerError(f"claim {claim_id} is {row[4]}, not pending")
        amount = float(row[1])
        playbook = str(row[2])
        income_acct = f"income:{playbook}"
        self.post(
            "income.verify",
            f"verified claim {claim_id}",
            [(ENERGY, amount, 0.0), (income_acct, 0.0, amount)],
            claim_id=claim_id,
        )
        self._conn.execute("UPDATE claims SET status='verified' WHERE id=?", (claim_id,))
        return Claim(claim_id, float(row[0]), amount, playbook, str(row[3]), "verified")

    def reject_claim(self, claim_id: str) -> Claim:
        row = self._conn.execute(
            "SELECT ts, amount_atp, playbook, evidence, status FROM claims WHERE id=?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown claim {claim_id}")
        if row[4] != "pending":
            raise LedgerError(f"claim {claim_id} is {row[4]}")
        self._conn.execute("UPDATE claims SET status='rejected' WHERE id=?", (claim_id,))
        return Claim(claim_id, float(row[0]), float(row[1]), str(row[2]), str(row[3]), "rejected")

    def claims(self, *, status: str | None = None) -> list[Claim]:
        if status:
            rows = self._conn.execute(
                "SELECT id, ts, amount_atp, playbook, evidence, status FROM claims "
                "WHERE status=? ORDER BY ts",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, ts, amount_atp, playbook, evidence, status FROM claims ORDER BY ts"
            ).fetchall()
        return [
            Claim(str(r[0]), float(r[1]), float(r[2]), str(r[3]), str(r[4]), str(r[5]))
            for r in rows
        ]

    def verified_income_atp(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(l.credit_atp),0) FROM lines l "
            "JOIN journal j ON j.id=l.journal_id "
            "WHERE l.account LIKE 'income:%' AND j.kind='income.verify'"
        ).fetchone()
        return float(row[0])

    def claimed_income_atp(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(amount_atp),0) FROM claims WHERE status='pending'"
        ).fetchone()
        return float(row[0])

    def daily_expense_atp(self, since: float) -> list[tuple[int, float]]:
        """Sum of expense credits to energy:balance, bucketed by UTC day number."""
        rows = self._conn.execute(
            "SELECT CAST((j.ts/86400) AS INTEGER) AS day, COALESCE(SUM(l.credit_atp),0) "
            "FROM lines l JOIN journal j ON j.id=l.journal_id "
            "WHERE l.account=? AND j.kind='expense' AND j.ts>=? "
            "GROUP BY day ORDER BY day",
            (ENERGY, since),
        ).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows]

    def snapshot(self) -> dict[str, Any]:
        return {
            "balance_atp": self.balance_atp(),
            "verified_income_atp": self.verified_income_atp(),
            "claimed_income_atp": self.claimed_income_atp(),
            "has_endowment": self.has_endowment(),
            "pending_claims": [c.to_dict() for c in self.claims(status="pending")],
        }
