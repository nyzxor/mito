"""DESIGN §5.1: three halt levels, sentinel channel, supervisor stops runtime + sandboxes in 2 s,
panic revokes vault handles and closes egress. The 2 s test uses a real child process."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from handbrake.kill.switch import (
    HaltLevel,
    KillSwitch,
    Supervisor,
)

pytestmark = pytest.mark.handbrake


class FakeKiller:
    def __init__(self) -> None:
        self.calls = 0

    def kill_all(self) -> int:
        self.calls += 1
        return 2


class FakeRevoker:
    def __init__(self) -> None:
        self.revoked = False

    def revoke_all(self) -> int:
        self.revoked = True
        return 3


class FakeEgress:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _supervisor(
    tmp_path: Path,
) -> tuple[Supervisor, KillSwitch, FakeKiller, FakeRevoker, FakeEgress]:
    ks = KillSwitch(tmp_path)
    killer, revoker, egress = FakeKiller(), FakeRevoker(), FakeEgress()
    events: list[tuple[str, dict[str, object]]] = []
    sup = Supervisor(
        ks,
        sandbox_killer=killer,
        vault_revoker=revoker,
        egress_closer=egress,
        audit=lambda k, p: events.append((k, p)),
    )
    return sup, ks, killer, revoker, egress


def _idle_child() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.05)"])  # noqa: S603


def test_sentinel_roundtrip(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path)
    assert ks.current() is None
    req = ks.request(HaltLevel.SOFT, "test")
    assert (tmp_path / "HALT").exists()
    cur = ks.current()
    assert (
        cur is not None
        and cur.level == HaltLevel.SOFT
        and cur.source == "test"
        and cur.ts == req.ts
    )
    ks.clear("operator")
    assert ks.current() is None


def test_sentinel_never_downgrades(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path)
    ks.request(HaltLevel.HARD, "a")
    ks.request(HaltLevel.SOFT, "b")
    cur = ks.current()
    assert cur is not None and cur.level == HaltLevel.HARD
    ks.request(HaltLevel.PANIC, "c")
    cur = ks.current()
    assert cur is not None and cur.level == HaltLevel.PANIC


def test_corrupt_sentinel_reads_as_panic(tmp_path: Path) -> None:
    (tmp_path / "HALT").write_text("garbage", encoding="utf-8")
    cur = KillSwitch(tmp_path).current()
    assert cur is not None and cur.level == HaltLevel.PANIC  # fail closed


def test_hard_halt_stops_real_runtime_within_2s(tmp_path: Path) -> None:
    sup, ks, killer, revoker, egress = _supervisor(tmp_path)
    child = _idle_child()
    try:
        sup.attach_runtime(child)
        ks.request(HaltLevel.HARD, "test")
        t0 = time.monotonic()
        report = sup.poll()
        elapsed = time.monotonic() - t0
        assert report is not None and report.level == HaltLevel.HARD
        assert report.runtime_stopped and child.poll() is not None
        assert elapsed < 2.0, f"hard halt took {elapsed:.2f}s"
        assert report.elapsed_s < 2.0
        assert killer.calls == 1
        assert not revoker.revoked and not egress.closed
    finally:
        if child.poll() is None:
            child.kill()


def test_soft_halt_leaves_runtime_running(tmp_path: Path) -> None:
    sup, ks, killer, _, _ = _supervisor(tmp_path)
    child = _idle_child()
    try:
        sup.attach_runtime(child)
        ks.request(HaltLevel.SOFT, "test")
        report = sup.poll()
        assert report is not None and report.level == HaltLevel.SOFT
        assert child.poll() is None  # runtime finishes its step and rests on its own
        assert killer.calls == 0
    finally:
        child.kill()


def test_panic_revokes_and_closes(tmp_path: Path) -> None:
    sup, ks, killer, revoker, egress = _supervisor(tmp_path)
    child = _idle_child()
    try:
        sup.attach_runtime(child)
        ks.request(HaltLevel.PANIC, "test")
        report = sup.poll()
        assert report is not None and report.level == HaltLevel.PANIC
        assert child.poll() is not None and killer.calls == 1
        assert revoker.revoked and egress.closed
        assert report.elapsed_s < 2.0
    finally:
        if child.poll() is None:
            child.kill()


def test_poll_executes_once_per_level_and_escalates(tmp_path: Path) -> None:
    sup, ks, killer, revoker, _ = _supervisor(tmp_path)
    ks.request(HaltLevel.SOFT, "t")
    assert sup.poll() is not None
    assert sup.poll() is None  # already executed at this level
    ks.request(HaltLevel.HARD, "t")
    r = sup.poll()
    assert r is not None and r.level == HaltLevel.HARD and killer.calls == 1
    assert sup.poll() is None
    ks.request(HaltLevel.PANIC, "t")
    r = sup.poll()
    assert r is not None and revoker.revoked


def test_supervisor_refuses_to_start_runtime_while_halted(tmp_path: Path) -> None:
    sup, ks, *_ = _supervisor(tmp_path)
    ks.request(HaltLevel.SOFT, "t")
    with pytest.raises(RuntimeError, match="HALT"):
        sup.start_runtime([sys.executable, "-c", "pass"])
