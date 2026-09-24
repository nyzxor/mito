"""Risk tiers T0–T5 and taint metadata loaded from policy/risk_tiers.toml (DESIGN §8)."""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIERS: tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4", "T5")


class TierError(ValueError):
    pass


@dataclass(frozen=True)
class TierMap:
    """tool (exact name or glob) -> tier, plus sensitivity/taint sets."""

    tools: dict[str, str]
    sensitivity_b: frozenset[str]
    untrusted_out: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> TierMap:
        with path.open("rb") as f:
            data = tomllib.load(f)
        tools: dict[str, str] = {}
        for tier in TIERS:
            block = data.get(tier)
            if not isinstance(block, dict) or "tools" not in block:
                raise TierError(f"{path}: missing [{tier}].tools")
            for tool in block["tools"]:
                if tool in tools:
                    raise TierError(f"{path}: tool {tool!r} appears in {tools[tool]} and {tier}")
                tools[str(tool)] = tier
        sens = data.get("sensitivity", {}).get("B", [])
        untrusted = data.get("taint_out", {}).get("UNTRUSTED", [])
        return cls(tools, frozenset(map(str, sens)), frozenset(map(str, untrusted)))

    def tier_of(self, tool: str) -> str | None:
        if tool in self.tools:
            return self.tools[tool]
        for pattern, tier in self.tools.items():
            if "*" in pattern and fnmatch.fnmatchcase(tool, pattern):
                return tier
        return None

    def is_sensitive(self, tool: str, args: dict[str, Any] | None = None) -> bool:
        base = tool.split(":")[0]
        if tool in self.sensitivity_b:
            return True
        if base in {s.split(":")[0] for s in self.sensitivity_b if ":" not in s}:
            return True
        if not args:
            return False
        for tag in self.sensitivity_b:
            if ":" not in tag:
                continue
            name, qualifier = tag.split(":", 1)
            if name != tool:
                continue
            val = args.get(qualifier)
            if val is True or val == qualifier or str(args.get("lane", "")) == qualifier:
                return True
        return False

    def emits_untrusted(self, tool: str) -> bool:
        return tool in self.untrusted_out


def tier_index(tier: str) -> int:
    try:
        return TIERS.index(tier)
    except ValueError as exc:
        raise TierError(f"unknown tier {tier!r}") from exc
