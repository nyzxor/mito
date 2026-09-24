"""Hardened Docker sandbox (ADR-0006). Fail closed if Docker is missing.

The Handbrake owns the docker CLI (the AST wiring test forbids subprocess in `mito/`).
T1 code-executing tools request a run through a live dispatch ticket; there is no host fallback.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Which = Callable[[str], str | None]
Run = Callable[..., subprocess.CompletedProcess[str]]

IMAGE_ENV = "MITO_SANDBOX_IMAGE"
DEFAULT_IMAGE = "mito-sandbox:dev"

HARDENED: tuple[str, ...] = (
    "--network",
    "none",
    "--user",
    "65532:65532",
    "--read-only",
    "--tmpfs",
    "/tmp:size=256m",  # noqa: S108 — container tmpfs, not host tempfile
    "--tmpfs",
    "/work:size=512m",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "256",
    "--memory",
    "1g",
    "--cpus",
    "1",
    "--ulimit",
    "nofile=1024",
)


class SandboxUnavailable(RuntimeError):
    """Docker missing or image missing — T1 code tools fail closed."""


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    argv: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def _norm(p: Path) -> str:
    return p.resolve().as_posix().rstrip("/").lower()


class DockerSandbox:
    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        which: Which = shutil.which,
        run: Run = subprocess.run,
    ) -> None:
        self.image = image
        self._which = which
        self._run = run

    def available(self) -> bool:
        return self._which("docker") is not None

    def run(
        self,
        *,
        session: str,
        workdir: Path,
        argv: Sequence[str],
        timeout_s: float = 15.0,
        workspace_root: Path | None = None,
    ) -> SandboxResult:
        docker = self._which("docker")
        if docker is None:
            raise SandboxUnavailable("sandbox unavailable; ask the operator (Docker is missing)")
        if not argv:
            raise SandboxUnavailable("sandbox refused empty argv")
        self._assert_workdir(workdir, workspace_root)
        cmd = [
            docker,
            "run",
            "--rm",
            *HARDENED,
            "--label",
            f"mito.session={session}",
            "--label",
            f"mito.run={session}",
            "-v",
            f"{workdir.resolve()}:/workspace:rw",
            "--workdir",
            "/workspace",
            self.image,
            *list(argv),
        ]
        try:
            proc = self._run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env={},  # no host env passthrough
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxUnavailable(
                f"sandbox wall-clock timeout ({timeout_s:.0f}s); ask the operator or shrink the job"
            ) from exc
        except OSError as exc:
            raise SandboxUnavailable(f"sandbox unavailable; ask the operator ({exc})") from exc
        return SandboxResult(int(proc.returncode), proc.stdout or "", proc.stderr or "", tuple(cmd))

    def _assert_workdir(self, workdir: Path, workspace_root: Path | None) -> None:
        work = workdir.resolve()
        if not work.is_dir():
            raise SandboxUnavailable("sandbox workdir does not exist")
        parts = {str(p).replace("\\", "/").lower() for p in work.parts}
        if parts & {"control", "handbrake", "policy"} or (
            "evals" in parts and "safety" in parts
        ):
            raise SandboxUnavailable("sandbox refused to mount a Handbrake-owned tree")
        if workspace_root is not None:
            root = workspace_root.resolve()
            try:
                work.relative_to(root)
            except ValueError as exc:
                raise SandboxUnavailable(
                    "sandbox workdir must stay inside the session workspace"
                ) from exc
        if "docker.sock" in str(work):
            raise SandboxUnavailable("sandbox refused docker.sock")


def build_image(repo_root: Path, *, tag: str = DEFAULT_IMAGE) -> int:
    docker = shutil.which("docker")
    if docker is None:
        raise SandboxUnavailable("sandbox unavailable; ask the operator (Docker is missing)")
    dockerfile = repo_root / "deploy" / "sandbox" / "Dockerfile"
    proc = subprocess.run(  # noqa: S603
        [docker, "build", "-t", tag, "-f", str(dockerfile), str(dockerfile.parent)],
        check=False,
    )
    return int(proc.returncode)
