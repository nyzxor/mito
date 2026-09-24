"""Autonomy ladder storage (DESIGN §11). A0 shadow, A1 supervised (default), A2 bounded.
Promotion only via a signed operator command (verified by the Handbrake core before `set`);
demotion is internal (brake trip, integrity mismatch, leash expiry). DEV MODE caps at A1."""

from __future__ import annotations

import json
import time
from pathlib import Path

LEVELS: tuple[str, ...] = ("A0", "A1", "A2")
DEFAULT = "A1"
DEV_MODE_MAX = "A1"


class AutonomyStore:
    def __init__(self, path: Path, *, dev_mode: bool = False) -> None:
        self.path = path
        self.dev_mode = dev_mode

    def get(self) -> str:
        level = DEFAULT
        if self.path.exists():
            try:
                level = str(json.loads(self.path.read_text(encoding="utf-8"))["level"])
            except (ValueError, KeyError, OSError):
                level = "A0"  # unreadable state: fail closed
        if level not in LEVELS:
            level = "A0"
        return self._cap(level)

    def set(self, level: str, *, by: str) -> str:
        if level not in LEVELS:
            raise ValueError(f"autonomy level must be one of {LEVELS}, got {level!r}")
        level = self._cap(level)
        self._write(level, by, "set")
        return level

    def demote(self, reason: str) -> str:
        cur = self.get()
        new = LEVELS[max(0, LEVELS.index(cur) - 1)]
        self._write(new, "handbrake", f"demote:{reason}")
        return new

    def _cap(self, level: str) -> str:
        if self.dev_mode and LEVELS.index(level) > LEVELS.index(DEV_MODE_MAX):
            return DEV_MODE_MAX
        return level

    def _write(self, level: str, by: str, action: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"level": level, "by": by, "action": action, "ts": time.time()}),
            encoding="utf-8",
        )
