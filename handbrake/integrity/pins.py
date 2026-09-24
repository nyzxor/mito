"""Integrity pins (DESIGN §5.10): signed sha256 of every file in the pinned trees."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from handbrake.canonical import canonical_json, sha256_hex
from handbrake.vault.keys import OperatorKey, verify

_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


def hash_tree(root: Path, rel_paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in rel_paths:
        p = root / rel
        if p.is_file():
            hashes[p.relative_to(root).as_posix()] = sha256_hex(p.read_bytes())
            continue
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*")):
            if not f.is_file() or f.suffix in _SKIP_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in f.relative_to(root).parts):
                continue
            hashes[f.relative_to(root).as_posix()] = sha256_hex(f.read_bytes())
    return hashes


@dataclass(frozen=True)
class Pins:
    hashes: dict[str, str]
    signed_at: float
    signature: str  # hex Ed25519 over canonical_json({hashes, signed_at})

    def message(self) -> bytes:
        return canonical_json({"hashes": self.hashes, "signed_at": self.signed_at}).encode("utf-8")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"hashes": self.hashes, "signed_at": self.signed_at, "signature": self.signature},
                indent=1,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> Pins:
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            {str(k): str(v) for k, v in d["hashes"].items()},
            float(d["signed_at"]),
            str(d["signature"]),
        )


def sign_pins(hashes: dict[str, str], key: OperatorKey, *, now: float | None = None) -> Pins:
    signed_at = time.time() if now is None else now
    unsigned = Pins(hashes, signed_at, "")
    return Pins(hashes, signed_at, key.sign(unsigned.message()).hex())


def verify_pins(pins: Pins, public_raw: bytes) -> bool:
    try:
        return verify(public_raw, pins.message(), bytes.fromhex(pins.signature))
    except ValueError:
        return False


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    reason: str
    changed: list[str]
    added: list[str]
    removed: list[str]
    checked_at: float


class IntegrityChecker:
    def __init__(
        self, repo_root: Path, pinned: list[str], pins_path: Path, public_raw: bytes
    ) -> None:
        self.repo_root = repo_root
        self.pinned = pinned
        self.pins_path = pins_path
        self.public_raw = public_raw

    def check(self) -> IntegrityResult:
        now = time.time()
        if not self.pins_path.exists():
            return IntegrityResult(
                False, "pins file missing; run `mito policy sign`", [], [], [], now
            )
        try:
            pins = Pins.load(self.pins_path)
        except (ValueError, KeyError, OSError) as exc:
            return IntegrityResult(False, f"pins unreadable: {exc}", [], [], [], now)
        if not verify_pins(pins, self.public_raw):
            return IntegrityResult(
                False, "pins signature invalid (not signed by the operator key)", [], [], [], now
            )
        current = hash_tree(self.repo_root, self.pinned)
        changed = sorted(k for k in current if k in pins.hashes and pins.hashes[k] != current[k])
        added = sorted(k for k in current if k not in pins.hashes)
        removed = sorted(k for k in pins.hashes if k not in current)
        ok = not (changed or added or removed)
        reason = (
            "ok" if ok else f"{len(changed)} changed, {len(added)} added, {len(removed)} removed"
        )
        return IntegrityResult(ok, reason, changed, added, removed, now)
