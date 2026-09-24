"""Append-only, hash-chained JSONL audit log with HMAC anchors (DESIGN §5.9, ADR-0007).

Record: {seq, ts, kind, payload, prev_hash, hash}
hash    = sha256(prev_hash + "\\n" + canonical_json({seq, ts, kind, payload}))
Anchor  : a regular record of kind "anchor" whose payload carries
          hmac_sha256(anchor_key, hash_of_previous_record). Anchors are emitted every
          `anchor_every` data records. A rewrite that recomputes every hash still fails
          because the attacker lacks the anchor key.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handbrake.canonical import canonical_json, sha256_hex

GENESIS = "0" * 64
ANCHOR_KIND = "anchor"


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: float
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "ts": self.ts,
                "kind": self.kind,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "hash": self.hash,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records: int
    last_hash: str
    first_bad_seq: int | None = None
    reason: str = ""


def _compute_hash(prev_hash: str, seq: int, ts: float, kind: str, payload: dict[str, Any]) -> str:
    body = canonical_json({"seq": seq, "ts": ts, "kind": kind, "payload": payload})
    return sha256_hex(prev_hash + "\n" + body)


def _hmac(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), "sha256").hexdigest()


class AuditChain:
    def __init__(self, path: Path, *, anchor_key: bytes, anchor_every: int = 100) -> None:
        if anchor_every < 1:
            raise ValueError("anchor_every must be >= 1")
        self.path = path
        self._key = anchor_key
        self._anchor_every = anchor_every
        self._seq = 0
        self._last_hash = GENESIS
        self._since_anchor = 0
        self._size = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load_tail()

    # ---- state -------------------------------------------------------------------------------
    def _load_tail(self) -> None:
        self._seq, self._last_hash, self._since_anchor = 0, GENESIS, 0
        if not self.path.exists():
            self._size = 0
            return
        self._size = self.path.stat().st_size
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                self._seq = int(rec["seq"])
                self._last_hash = str(rec["hash"])
                if rec["kind"] == ANCHOR_KIND:
                    self._since_anchor = 0
                else:
                    self._since_anchor += 1

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def last_hash(self) -> str:
        return self._last_hash

    # ---- write -------------------------------------------------------------------------------
    def append(self, kind: str, payload: dict[str, Any]) -> AuditRecord:
        if kind == ANCHOR_KIND:
            raise ValueError("kind 'anchor' is reserved")
        rec = self._write(kind, payload)
        self._since_anchor += 1
        if self._since_anchor >= self._anchor_every:
            self._write(ANCHOR_KIND, {"anchor_of": rec.hash, "hmac": _hmac(self._key, rec.hash)})
            self._since_anchor = 0
        return rec

    def _write(self, kind: str, payload: dict[str, Any]) -> AuditRecord:
        # Another trusted process (CLI while the server runs) may have appended: re-sync the tail
        # so the chain never forks. Concurrent writers are still serialized by the operator
        # convention "CLI goes through the API when the server is up".
        if self.path.exists() and self.path.stat().st_size != self._size:
            self._load_tail()
        seq = self._seq + 1
        ts = time.time()
        h = _compute_hash(self._last_hash, seq, ts, kind, payload)
        rec = AuditRecord(seq, ts, kind, payload, self._last_hash, h)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(rec.to_json() + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._size = self.path.stat().st_size
        self._seq = seq
        self._last_hash = h
        return rec

    # ---- verify ------------------------------------------------------------------------------
    def verify(self, *, expected_min_records: int | None = None) -> VerifyResult:
        if not self.path.exists():
            return VerifyResult(True, 0, GENESIS)
        prev = GENESIS
        count = 0
        since_anchor = 0
        with self.path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    return VerifyResult(False, count, prev, count + 1, "malformed record")
                seq = int(rec["seq"])
                if seq != count + 1:
                    return VerifyResult(False, count, prev, seq, f"sequence gap at {seq}")
                if rec["prev_hash"] != prev:
                    return VerifyResult(
                        False, count, prev, seq, "prev_hash mismatch (chain broken)"
                    )
                expected = _compute_hash(prev, seq, float(rec["ts"]), rec["kind"], rec["payload"])
                if rec["hash"] != expected:
                    return VerifyResult(False, count, prev, seq, "record hash mismatch")
                if rec["kind"] == ANCHOR_KIND:
                    p = rec["payload"]
                    if p.get("anchor_of") != prev or not hmac.compare_digest(
                        str(p.get("hmac", "")), _hmac(self._key, prev)
                    ):
                        return VerifyResult(False, count, prev, seq, "anchor hmac mismatch")
                    since_anchor = 0
                else:
                    since_anchor += 1
                    if since_anchor > self._anchor_every:
                        return VerifyResult(False, count, prev, seq, "missing anchor (truncation?)")
                prev = rec["hash"]
                count = seq
        if expected_min_records is not None and count < expected_min_records:
            return VerifyResult(False, count, prev, count, "chain truncated below expected length")
        return VerifyResult(True, count, prev)
