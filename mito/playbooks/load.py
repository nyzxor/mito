"""Playbook TOML loader (DESIGN §10). Blacklisted categories refuse to load."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handbrake.schedule.cron import CronError, parse_cron


class PlaybookError(ValueError):
    pass


@dataclass(frozen=True)
class Playbook:
    name: str
    enabled: bool
    autonomy_max: str
    tools: tuple[str, ...]
    max_tier_auto: str
    cron: str
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "autonomy_max": self.autonomy_max,
            "tools": list(self.tools),
            "max_tier_auto": self.max_tier_auto,
            "cron": self.cron,
        }


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(_strings(value))
        return out
    if isinstance(node, list):
        out = []
        for value in node:
            out.extend(_strings(value))
        return out
    return []


def blacklist_categories(policy_dir: Path) -> frozenset[str]:
    with (policy_dir / "blacklist.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return frozenset(str(c) for c in data.get("categories", []))


def load_playbook(path: Path, *, categories: frozenset[str]) -> Playbook:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    declared = set(_strings(data))
    hits = sorted(categories & declared)
    if hits:
        raise PlaybookError(f"{path.name} declares blacklisted categories {hits}")
    tools = data.get("tools", {})
    required = tuple(str(t) for t in tools.get("required", []))
    cron = str(data.get("schedule", {}).get("cron", "@daily"))
    try:
        parse_cron(cron)
    except CronError as exc:
        raise PlaybookError(f"{path.name}: bad cron {cron!r}") from exc
    name = str(data.get("name") or path.stem)
    return Playbook(
        name=name,
        enabled=bool(data.get("enabled", False)),
        autonomy_max=str(data.get("autonomy_max", "A1")),
        tools=required,
        max_tier_auto=str(tools.get("max_tier_auto", "T0")),
        cron=cron,
        path=path,
    )


def load_dir(root: Path, *, categories: frozenset[str]) -> list[Playbook]:
    books = [load_playbook(p, categories=categories) for p in sorted(root.glob("*.toml"))]
    names = [b.name for b in books]
    if len(names) != len(set(names)):
        raise PlaybookError(f"duplicate playbook names in {root}")
    return books
