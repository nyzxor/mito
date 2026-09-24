"""DESIGN §5.10 integrity pins + ADR-0007 operator key and signed commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from handbrake.integrity.commands import CommandError, NonceStore, SignedCommand
from handbrake.integrity.pins import IntegrityChecker, Pins, hash_tree, sign_pins, verify_pins
from handbrake.vault.keys import OperatorKey, verify

pytestmark = pytest.mark.handbrake


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "handbrake").mkdir()
    (tmp_path / "handbrake" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "handbrake" / "__pycache__").mkdir()
    (tmp_path / "handbrake" / "__pycache__" / "a.pyc").write_bytes(b"\x00")
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy" / "policy.toml").write_text("x=1\n", encoding="utf-8")
    return tmp_path


def test_operator_key_sign_verify_roundtrip() -> None:
    key = OperatorKey.generate()
    sig = key.sign(b"hello")
    assert verify(key.public_bytes(), b"hello", sig)
    assert not verify(key.public_bytes(), b"hellp", sig)
    again = OperatorKey.from_bytes(key.to_bytes())
    assert again.public_bytes() == key.public_bytes()


def test_hash_tree_ignores_caches_and_is_relative(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    hashes = hash_tree(root, ["handbrake", "policy", "missing-dir"])
    assert set(hashes) == {"handbrake/a.py", "policy/policy.toml"}


def test_pins_sign_verify_and_detect_change(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    key = OperatorKey.generate()
    pins = sign_pins(hash_tree(root, ["handbrake", "policy"]), key)
    pins_path = tmp_path / "pins.json"
    pins.save(pins_path)
    loaded = Pins.load(pins_path)
    assert verify_pins(loaded, key.public_bytes())

    checker = IntegrityChecker(root, ["handbrake", "policy"], pins_path, key.public_bytes())
    assert checker.check().ok

    (root / "policy" / "policy.toml").write_text("x=2\n", encoding="utf-8")
    res = checker.check()
    assert not res.ok and res.changed == ["policy/policy.toml"]

    (root / "handbrake" / "b.py").write_text("", encoding="utf-8")
    res = checker.check()
    assert not res.ok and "handbrake/b.py" in res.added


def test_pins_with_wrong_key_or_tampered_hash_fail(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    key, other = OperatorKey.generate(), OperatorKey.generate()
    pins = sign_pins(hash_tree(root, ["handbrake"]), key)
    assert not verify_pins(pins, other.public_bytes())
    forged = Pins({**pins.hashes, "handbrake/a.py": "00" * 32}, pins.signed_at, pins.signature)
    assert not verify_pins(forged, key.public_bytes())
    pins_path = tmp_path / "pins.json"
    forged.save(pins_path)
    res = IntegrityChecker(root, ["handbrake"], pins_path, key.public_bytes()).check()
    assert not res.ok and "signature" in res.reason


def test_missing_pins_is_a_failure(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    res = IntegrityChecker(root, ["handbrake"], tmp_path / "nope.json", b"\x00" * 32).check()
    assert not res.ok and "missing" in res.reason


def test_signed_command_roundtrip_and_replay_protection(tmp_path: Path) -> None:
    key = OperatorKey.generate()
    nonces = NonceStore(tmp_path / "hb.sqlite")
    cmd = SignedCommand.build("autonomy.set", {"level": "A2"}, key, now=1000.0)
    assert cmd.verify(key.public_bytes(), nonces, now=1010.0) == {"level": "A2"}
    with pytest.raises(CommandError, match="replay"):
        cmd.verify(key.public_bytes(), nonces, now=1011.0)


def test_signed_command_rejects_stale_and_forged(tmp_path: Path) -> None:
    key, other = OperatorKey.generate(), OperatorKey.generate()
    nonces = NonceStore(tmp_path / "hb.sqlite")
    stale = SignedCommand.build("policy.sign", {}, key, now=1000.0)
    with pytest.raises(CommandError, match="stale"):
        stale.verify(key.public_bytes(), nonces, now=1000.0 + 600)
    forged = SignedCommand.build("autonomy.set", {"level": "A2"}, other, now=1000.0)
    with pytest.raises(CommandError, match="signature"):
        forged.verify(key.public_bytes(), nonces, now=1001.0)
    tampered = SignedCommand(
        cmd="autonomy.set",
        args={"level": "A2"},
        nonce=stale.nonce,
        ts=stale.ts,
        signature=stale.signature,
    )
    with pytest.raises(CommandError, match="signature"):
        tampered.verify(key.public_bytes(), nonces, now=1001.0)


def test_signed_command_serializes(tmp_path: Path) -> None:
    key = OperatorKey.generate()
    cmd = SignedCommand.build("ledger.confirm", {"id": "c1"}, key, now=5.0)
    again = SignedCommand.from_dict(cmd.to_dict())
    assert again == cmd
