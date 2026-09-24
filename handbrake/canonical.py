"""Canonical serialization and hashing shared by audit, approvals, pins and signed commands."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 preserved."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def action_hash(tool: str, args: dict[str, Any], session: str, taint: list[str]) -> str:
    """Identity of a proposed action for approval binding (DESIGN §5.8)."""
    return sha256_hex(
        canonical_json({"tool": tool, "args": args, "session": session, "taint": sorted(taint)})
    )
