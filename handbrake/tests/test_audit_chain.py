"""DESIGN §5.9: append-only, hash-chained JSONL with HMAC anchors; verify detects tampering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from handbrake.audit.chain import AuditChain, VerifyResult
from handbrake.canonical import canonical_json, sha256_hex

pytestmark = pytest.mark.handbrake

KEY = b"test-anchor-key"


def _chain(tmp_path: Path, anchor_every: int = 3) -> AuditChain:
    return AuditChain(tmp_path / "audit.jsonl", anchor_key=KEY, anchor_every=anchor_every)


def test_canonical_json_is_deterministic() -> None:
    a = canonical_json({"b": 1, "a": [3, {"z": 1, "y": 2}]})
    b = canonical_json({"a": [3, {"y": 2, "z": 1}], "b": 1})
    assert a == b == '{"a":[3,{"y":2,"z":1}],"b":1}'
    assert sha256_hex("x") == sha256_hex(b"x")


def test_append_links_records_and_verify_passes(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    r1 = chain.append("proposal", {"tool": "time"})
    r2 = chain.append("verdict", {"kind": "allow"})
    assert r1.seq == 1 and r2.seq == 2
    assert r1.prev_hash == "0" * 64
    assert r2.prev_hash == r1.hash
    res = chain.verify()
    assert res.ok and res.records == 2


def test_verify_survives_reopen(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    for i in range(7):
        chain.append("k", {"i": i})
    reopened = _chain(tmp_path)
    r = reopened.append("k", {"i": 7})
    assert r.seq == 8 + 2  # two anchors were interleaved (after 3 and 6 records)
    assert reopened.verify().ok


def test_anchor_records_are_emitted_and_chained(tmp_path: Path) -> None:
    chain = _chain(tmp_path, anchor_every=2)
    chain.append("k", {})
    chain.append("k", {})
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["k", "k", "anchor"]
    assert chain.verify().ok


def test_tampered_payload_is_detected(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    chain.append("k", {"cost": 1})
    chain.append("k", {"cost": 2})
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["cost"] = 0
    lines[0] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = _chain(tmp_path).verify()
    assert not res.ok and res.first_bad_seq == 1 and "hash" in res.reason


def test_truncation_after_anchor_is_detected(tmp_path: Path) -> None:
    chain = _chain(tmp_path, anchor_every=2)
    for _ in range(4):
        chain.append("k", {})
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    # drop the last two records (one data record + its anchor) -> chain still self-consistent,
    # so only the anchor bookkeeping can catch it.
    path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
    res = _chain(tmp_path).verify(expected_min_records=len(lines))
    assert not res.ok and "truncat" in res.reason


def test_recomputed_chain_without_key_fails_anchor_hmac(tmp_path: Path) -> None:
    """An attacker who rewrites records and recomputes all hashes still cannot forge anchors."""
    chain = _chain(tmp_path, anchor_every=2)
    chain.append("k", {"v": 1})
    chain.append("k", {"v": 2})
    path = tmp_path / "audit.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["payload"]["v"] = 99
    prev = "0" * 64
    for rec in records:
        rec["prev_hash"] = prev
        body = {k: rec[k] for k in ("seq", "ts", "kind", "payload")}
        rec["hash"] = sha256_hex(prev + "\n" + canonical_json(body))
        prev = rec["hash"]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    res = _chain(tmp_path).verify()
    assert not res.ok and "anchor" in res.reason


def test_verify_result_reports_last_hash(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    r = chain.append("k", {})
    res: VerifyResult = chain.verify()
    assert res.last_hash == r.hash
