"""T-sandbox-fail-closed and hardened run flags (ADR-0006, THREAT_MODEL)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from handbrake.sandbox.runner import HARDENED, DockerSandbox, SandboxUnavailable

pytestmark = pytest.mark.handbrake


def test_sandbox_fail_closed_without_docker(tmp_path: Path) -> None:
    sb = DockerSandbox(which=lambda _n: None)
    with pytest.raises(SandboxUnavailable, match="Docker is missing"):
        sb.run(session="s", workdir=tmp_path, argv=["python", "-c", "print(1)"])


def test_sandbox_run_uses_hardened_flags(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    work = tmp_path / "workspace" / "s1"
    work.mkdir(parents=True)
    sb = DockerSandbox(which=lambda n: "/usr/bin/docker" if n == "docker" else None, run=fake_run)
    result = sb.run(
        session="sess-1",
        workdir=work,
        argv=["python", "-c", "print(1)"],
        workspace_root=tmp_path / "workspace",
    )
    assert result.exit_code == 0 and result.stdout == "ok\n"
    cmd = captured[0]
    assert cmd[0] == "/usr/bin/docker" and cmd[1] == "run"
    for flag in HARDENED:
        assert flag in cmd
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert f"{work.resolve()}:/workspace:rw" in cmd
    assert "mito.session=sess-1" in cmd
    assert not any("docker.sock" in a for a in cmd)
    assert "--env" not in cmd and "-e" not in cmd


def test_sandbox_never_mounts_control_or_repo(tmp_path: Path) -> None:
    sb = DockerSandbox(
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        run=lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""),
    )
    control = tmp_path / "control"
    control.mkdir()
    with pytest.raises(SandboxUnavailable, match="Handbrake-owned"):
        sb.run(session="s", workdir=control, argv=["true"])
    outside = tmp_path / "other"
    outside.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(SandboxUnavailable, match="workspace"):
        sb.run(session="s", workdir=outside, argv=["true"], workspace_root=ws)


def test_sandbox_empty_argv_refused(tmp_path: Path) -> None:
    sb = DockerSandbox(which=lambda n: "/bin/docker" if n == "docker" else None)
    with pytest.raises(SandboxUnavailable, match="empty argv"):
        sb.run(session="s", workdir=tmp_path, argv=[])
