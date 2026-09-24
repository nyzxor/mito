"""The Handbrake process (ADR-0002): serves the API on loopback, ticks the supervisor/leash/
integrity every 250 ms, and owns the runtime child. Returns when a hard/panic halt executed."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from handbrake.api.app import create_app
from handbrake.core import Handbrake
from handbrake.kill.switch import HaltLevel
from handbrake.paths import MitoPaths

TICK_S = 0.25


def parse_bind(bind: str) -> tuple[str, int]:
    host, _, port = bind.rpartition(":")
    host = host or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(
        "MITO_ALLOW_NONLOOPBACK_BIND"
    ):
        raise ValueError(
            f"Handbrake must bind to loopback, got {host!r} "
            "(set MITO_ALLOW_NONLOOPBACK_BIND=1 only inside compose)"
        )
    return host, int(port or 8710)


def runtime_argv(paths: MitoPaths) -> list[str]:
    return [sys.executable, "-m", "mito.runtime_main"]


async def run_handbrake(
    paths: MitoPaths,
    repo_root: Path,
    *,
    bind: str = "127.0.0.1:8710",
    start_runtime: bool = True,
    argv: Sequence[str] | None = None,
    max_seconds: float | None = None,
) -> str:
    host, port = parse_bind(bind)
    hb = Handbrake(paths, repo_root)
    integrity = hb.integrity_check(force=True)
    if not integrity.ok:
        print(
            f"[handbrake] INTEGRITY MISMATCH: {integrity.reason} — frozen; "
            "run `mito policy sign` if the change is yours",
            file=sys.stderr,
        )
    if hb.dev_mode:
        print(
            "[handbrake] DEV MODE: weaker process isolation; autonomy capped at A1", file=sys.stderr
        )
    if hb.halted() is not None:
        print(
            "[handbrake] HALT sentinel present; runtime will not start (use `mito wake`)",
            file=sys.stderr,
        )

    config = uvicorn.Config(
        create_app(hb), host=host, port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    hb.audit.append(
        "handbrake.start",
        {"bind": f"{host}:{port}", "dev_mode": hb.dev_mode, "integrity_ok": integrity.ok},
    )

    if start_runtime and hb.halted() is None and not hb.frozen:
        env = {
            **os.environ,
            "MITO_HANDBRAKE_URL": f"http://{host}:{port}",
            "MITO_RUNTIME_TOKEN": hb.tokens()["runtime"],
            "MITO_HOME": str(paths.home),
        }
        hb.supervisor.start_runtime(list(argv or runtime_argv(paths)), env=env)

    reason = "stopped"
    loop = asyncio.get_running_loop()
    deadline = None if max_seconds is None else loop.time() + max_seconds
    try:
        while True:
            report = hb.tick()
            if report["halt"] in (HaltLevel.HARD.value, HaltLevel.PANIC.value):
                reason = f"halt:{report['halt']}"
                break
            rt = hb.supervisor.runtime
            if rt is not None and rt.poll() is not None and hb.halted() is None:
                hb.audit.append("runtime.exit", {"code": rt.returncode})
                # watchdog: restart only if not halted/frozen; rate-limited by the tick
                await asyncio.sleep(2.0)
                if hb.halted() is None and not hb.frozen and start_runtime:
                    hb.supervisor.start_runtime(
                        list(argv or runtime_argv(paths)),
                        env={
                            **os.environ,
                            "MITO_HANDBRAKE_URL": f"http://{host}:{port}",
                            "MITO_RUNTIME_TOKEN": hb.tokens()["runtime"],
                            "MITO_HOME": str(paths.home),
                        },
                    )
            if deadline is not None and loop.time() > deadline:
                reason = "max_seconds"
                break
            await asyncio.sleep(TICK_S)
    finally:
        if hb.supervisor.runtime is not None and hb.supervisor.runtime.poll() is None:
            hb.supervisor.execute(HaltLevel.HARD, source="handbrake-shutdown")
        server.should_exit = True
        await server_task
        hb.audit.append("handbrake.stop", {"reason": reason})
    return reason
