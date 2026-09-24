"""Kill switch and supervisor (DESIGN §5.1).

Levels: soft = finish current step then Deep Rest; hard = stop runtime + sandboxes within 2 s;
panic = hard + revoke vault handles + close egress. The sentinel file `control/HALT` is the
common channel: CLI, HTTP API and chat commands all write it; the supervisor polls it. The
runtime polls it too (to finish its step on soft) but can never remove it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

SENTINEL_NAME = "HALT"
HARD_DEADLINE_S = 2.0


class HaltLevel(StrEnum):
    SOFT = "soft"
    HARD = "hard"
    PANIC = "panic"

    @property
    def rank(self) -> int:
        return {"soft": 0, "hard": 1, "panic": 2}[self.value]


@dataclass(frozen=True)
class HaltRequest:
    level: HaltLevel
    source: str
    ts: float


@dataclass(frozen=True)
class HaltReport:
    level: HaltLevel
    elapsed_s: float
    runtime_stopped: bool
    sandboxes_killed: int
    handles_revoked: int
    egress_closed: bool


class SandboxKiller(Protocol):
    def kill_all(self) -> int: ...


class VaultRevoker(Protocol):
    def revoke_all(self) -> int: ...


class EgressCloser(Protocol):
    def close(self) -> None: ...


class NoopSandboxKiller:
    def kill_all(self) -> int:
        return 0


class DockerSandboxKiller:
    """Kills every container labeled `mito.session`. Tolerates a missing Docker daemon."""

    def __init__(self, label: str = "mito.session") -> None:
        self.label = label

    def kill_all(self) -> int:
        docker = shutil.which("docker")
        if docker is None:
            return 0
        try:
            out = subprocess.run(  # noqa: S603
                [docker, "ps", "-q", "--filter", f"label={self.label}"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            ).stdout.split()
            if out:
                subprocess.run(  # noqa: S603
                    [docker, "kill", *out], capture_output=True, timeout=1.5, check=False
                )
            return len(out)
        except (OSError, subprocess.SubprocessError):
            return 0


class KillSwitch:
    def __init__(self, control_dir: Path) -> None:
        self.control_dir = control_dir
        self.sentinel = control_dir / SENTINEL_NAME
        control_dir.mkdir(parents=True, exist_ok=True)

    def current(self) -> HaltRequest | None:
        if not self.sentinel.exists():
            return None
        try:
            data = json.loads(self.sentinel.read_text(encoding="utf-8"))
            return HaltRequest(
                HaltLevel(str(data["level"])), str(data.get("source", "?")), float(data["ts"])
            )
        except (ValueError, KeyError, TypeError, OSError):
            # Unreadable sentinel: fail closed at the highest level.
            return HaltRequest(HaltLevel.PANIC, "corrupt-sentinel", time.time())

    def request(self, level: HaltLevel, source: str) -> HaltRequest:
        existing = self.current()
        if existing is not None and existing.level.rank >= level.rank:
            return existing
        req = HaltRequest(level, source, time.time())
        tmp = self.sentinel.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"level": req.level.value, "source": req.source, "ts": req.ts}),
            encoding="utf-8",
        )
        os.replace(tmp, self.sentinel)
        return req

    def clear(self, by: str) -> None:
        """Operator-only. The runtime process never calls this."""
        if self.sentinel.exists():
            self.sentinel.unlink()
        (self.control_dir / "HALT.cleared").write_text(
            json.dumps({"by": by, "ts": time.time()}), encoding="utf-8"
        )


class Supervisor:
    """Owns the runtime child process and executes halts. Parent -> child only, never reverse."""

    def __init__(
        self,
        kill_switch: KillSwitch,
        *,
        sandbox_killer: SandboxKiller,
        vault_revoker: VaultRevoker,
        egress_closer: EgressCloser,
        audit: Callable[[str, dict[str, Any]], object],
    ) -> None:
        self.kill_switch = kill_switch
        self._killer = sandbox_killer
        self._revoker = vault_revoker
        self._egress = egress_closer
        self._audit = audit
        self._runtime: subprocess.Popen[bytes] | None = None
        self._executed_rank = -1

    def attach_runtime(self, proc: subprocess.Popen[bytes]) -> None:
        self._runtime = proc

    def start_runtime(self, argv: Sequence[str], **popen_kwargs: Any) -> subprocess.Popen[bytes]:
        if self.kill_switch.current() is not None:
            raise RuntimeError(
                "HALT sentinel present; clear it with `mito wake` before starting the runtime"
            )
        proc: subprocess.Popen[bytes] = subprocess.Popen(list(argv), **popen_kwargs)  # noqa: S603
        self._runtime = proc
        self._executed_rank = -1
        self._audit("runtime.start", {"pid": proc.pid})
        return proc

    @property
    def runtime(self) -> subprocess.Popen[bytes] | None:
        return self._runtime

    def poll(self) -> HaltReport | None:
        req = self.kill_switch.current()
        if req is None or req.level.rank <= self._executed_rank:
            return None
        report = self.execute(req.level, source=req.source)
        self._executed_rank = req.level.rank
        return report

    def execute(self, level: HaltLevel, *, source: str = "direct") -> HaltReport:
        t0 = time.monotonic()
        self.kill_switch.request(level, source)
        stopped = False
        killed = 0
        revoked = 0
        closed = False
        if level.rank >= HaltLevel.HARD.rank:
            stopped = self._stop_runtime(deadline=t0 + HARD_DEADLINE_S)
            killed = self._killer.kill_all()
        if level == HaltLevel.PANIC:
            revoked = self._revoker.revoke_all()
            self._egress.close()
            closed = True
        report = HaltReport(level, time.monotonic() - t0, stopped, killed, revoked, closed)
        payload: dict[str, Any] = {"source": source, **report.__dict__}
        payload["level"] = level.value
        self._audit("halt", payload)
        return report

    def _stop_runtime(self, *, deadline: float) -> bool:
        proc = self._runtime
        if proc is None or proc.poll() is not None:
            return True
        proc.terminate()
        try:
            proc.wait(timeout=max(0.05, min(0.8, deadline - time.monotonic())))
            return True
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=max(0.05, deadline - time.monotonic()))
                return True
            except subprocess.TimeoutExpired:
                return False
