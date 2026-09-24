"""T1 code-executing tools. Execution happens in the Handbrake's Docker sandbox."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.handbrake_client import HandbrakeClient
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class CodeRunParams(BaseModel):
    code: str = Field(..., description="Python source to run inside the sandbox.")
    timeout_s: float = Field(15.0, ge=1, le=60)


class ShellParams(BaseModel):
    argv: list[str] = Field(..., description="Argv executed inside the sandbox (no host shell).")
    timeout_s: float = Field(15.0, ge=1, le=60)


class GitLocalParams(BaseModel):
    argv: list[str] = Field(..., description="git subcommand, e.g. ['status']. No push/remote.")
    timeout_s: float = Field(15.0, ge=1, le=60)


_BLOCKED_GIT = frozenset({"push", "remote", "clone", "fetch", "pull"})


def sandbox_tools(handbrake: HandbrakeClient) -> list[Tool]:
    async def code_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, CodeRunParams):
            raise ToolError("internal: code.run params mismatch")
        return await handbrake.sandbox_run(
            d.ticket, ["python", "-c", p.code], timeout_s=p.timeout_s
        )

    async def shell_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, ShellParams):
            raise ToolError("internal: shell params mismatch")
        if not p.argv:
            raise ToolError("shell argv must be a non-empty list of strings.")
        return await handbrake.sandbox_run(d.ticket, p.argv, timeout_s=p.timeout_s)

    async def git_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, GitLocalParams):
            raise ToolError("internal: git.local params mismatch")
        if not p.argv:
            raise ToolError("git.local argv must be a non-empty list.")
        if any(a.lstrip("-").lower() in _BLOCKED_GIT for a in p.argv):
            raise ToolError(
                "git.local cannot talk to remotes. Use git.push (T3, own repos) after approval."
            )
        return await handbrake.sandbox_run(d.ticket, ["git", *p.argv], timeout_s=p.timeout_s)

    return [
        Tool(
            "code.run",
            "Run Python in a no-network Docker sandbox. Fail-closed if Docker is missing.",
            CodeRunParams,
            code_h,
            "T1",
        ),
        Tool(
            "shell",
            "Run argv in the no-network sandbox. Not a host shell. Docker required.",
            ShellParams,
            shell_h,
            "T1",
            idempotent=False,
        ),
        Tool(
            "git.local",
            "Local git (status/add/commit/diff) inside the sandbox. No remotes.",
            GitLocalParams,
            git_h,
            "T1",
        ),
    ]
