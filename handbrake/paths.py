"""State directory layout (DESIGN §4.2): `$MITO_HOME/control` is Handbrake-owned,
`$MITO_HOME/runtime` is agent-owned. Never inside the repo."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

HOME_ENV = "MITO_HOME"
DEV_MODE_ENV = "MITO_DEV_MODE"


@dataclass(frozen=True)
class MitoPaths:
    home: Path

    @classmethod
    def from_env(cls) -> MitoPaths:
        raw = os.environ.get(HOME_ENV, "~/.mito")
        return cls(Path(raw).expanduser().resolve())

    @property
    def control(self) -> Path:
        return self.home / "control"

    @property
    def runtime(self) -> Path:
        return self.home / "runtime"

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    def ensure(self) -> None:
        for p in (self.control, self.runtime, self.workspace):
            p.mkdir(parents=True, exist_ok=True)


def repo_root() -> Path:
    """The checkout that contains handbrake/, policy/ and config/ (editable install)."""
    return Path(__file__).resolve().parents[1]


def detect_dev_mode() -> bool:
    """Bare Windows or explicit flag. Containers on Linux run in production posture."""
    flag = os.environ.get(DEV_MODE_ENV, "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return sys.platform == "win32"
