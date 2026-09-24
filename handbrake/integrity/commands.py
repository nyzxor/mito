"""Signed operator commands (ADR-0007): {cmd, args, nonce, ts} signed with the operator key;
the Handbrake verifies signature, freshness (±5 min) and nonce uniqueness."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handbrake.canonical import canonical_json
from handbrake.vault.keys import OperatorKey, verify

MAX_SKEW_S = 300.0


class CommandError(ValueError):
    pass


class NonceStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS nonces(nonce TEXT PRIMARY KEY, ts REAL NOT NULL)"
        )

    def use(self, nonce: str, ts: float) -> bool:
        """Returns False if the nonce was already used."""
        try:
            self._conn.execute("INSERT INTO nonces(nonce, ts) VALUES(?,?)", (nonce, ts))
            return True
        except sqlite3.IntegrityError:
            return False


@dataclass(frozen=True)
class SignedCommand:
    cmd: str
    args: dict[str, Any]
    nonce: str
    ts: float
    signature: str  # hex

    def message(self) -> bytes:
        return canonical_json(
            {"cmd": self.cmd, "args": self.args, "nonce": self.nonce, "ts": self.ts}
        ).encode()

    @classmethod
    def build(
        cls, cmd: str, args: dict[str, Any], key: OperatorKey, *, now: float | None = None
    ) -> SignedCommand:
        ts = time.time() if now is None else now
        unsigned = cls(cmd, args, uuid.uuid4().hex, ts, "")
        return cls(cmd, args, unsigned.nonce, ts, key.sign(unsigned.message()).hex())

    def verify(
        self, public_raw: bytes, nonces: NonceStore, *, now: float | None = None
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        try:
            sig = bytes.fromhex(self.signature)
        except ValueError as exc:
            raise CommandError("bad signature encoding") from exc
        if not verify(public_raw, self.message(), sig):
            raise CommandError("signature invalid")
        if abs(now - self.ts) > MAX_SKEW_S:
            raise CommandError("command stale (outside ±5 min window)")
        if not nonces.use(self.nonce, self.ts):
            raise CommandError("nonce already used (replay)")
        return self.args

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd": self.cmd,
            "args": self.args,
            "nonce": self.nonce,
            "ts": self.ts,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SignedCommand:
        return cls(
            str(d["cmd"]), dict(d["args"]), str(d["nonce"]), float(d["ts"]), str(d["signature"])
        )
