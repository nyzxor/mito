"""End-to-end: the Handbrake process serves the API, supervises a real runtime child, and a hard
halt written to the sentinel stops both within the deadline."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

from handbrake.api.server import parse_bind, run_handbrake
from handbrake.core import Handbrake
from handbrake.kill.switch import HaltLevel, KillSwitch
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo

pytestmark = pytest.mark.handbrake


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_parse_bind_refuses_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MITO_ALLOW_NONLOOPBACK_BIND", raising=False)
    assert parse_bind("127.0.0.1:8710") == ("127.0.0.1", 8710)
    with pytest.raises(ValueError, match="loopback"):
        parse_bind("0.0.0.0:8710")


async def test_server_supervises_runtime_and_hard_halt_stops_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    port = _free_port()
    idle = [sys.executable, "-c", "import time\nwhile True: time.sleep(0.05)"]
    task = asyncio.create_task(
        run_handbrake(paths, repo, bind=f"127.0.0.1:{port}", argv=idle, max_seconds=20)
    )
    tokens = json.loads((paths.control / "tokens.json").read_text(encoding="utf-8"))
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Authorization": f"Bearer {tokens['runtime']}"},
    ) as c:
        for _ in range(100):
            try:
                r = await c.get("/state")
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
        else:
            pytest.fail("server did not come up")
        assert r.json()["halt"] is None
        await asyncio.sleep(0.4)  # let the supervisor start the child
        # the sentinel channel: any operator surface can write it
        t0 = time.monotonic()
        KillSwitch(paths.control).request(HaltLevel.HARD, "test-sentinel")
        reason = await asyncio.wait_for(task, timeout=10)
        elapsed = time.monotonic() - t0
    assert reason == "halt:hard"
    assert elapsed < 3.0, f"hard halt via sentinel took {elapsed:.2f}s end-to-end"
    hb = Handbrake(paths, repo, dev_mode=True)
    assert hb.supervisor.runtime is None or hb.supervisor.runtime.poll() is not None
    kinds = [rec.kind for rec in hb.audit_tail(50)]
    assert "runtime.start" in kinds and "halt" in kinds and "handbrake.stop" in kinds
    assert hb.audit.verify().ok
