"""T1 workspace filesystem tools. Confined by policy *and* by the handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class FsReadParams(BaseModel):
    path: str = Field(..., description="Absolute path inside the session workspace.")


class FsWriteParams(BaseModel):
    path: str
    content: str = ""


class FsListParams(BaseModel):
    path: str = Field(..., description="Directory inside the session workspace.")


def _inside(path: str, workspace: str) -> Path:
    ws = Path(workspace).resolve()
    target = Path(path).resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ToolError(
            f"{target} is outside the workspace {ws}. Use a path under the workspace."
        ) from exc
    return target


def fs_tools(workspace: str) -> list[Tool]:
    async def read_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, FsReadParams):
            raise ToolError("internal: fs.read params mismatch")
        target = _inside(p.path, workspace)
        if not target.is_file():
            raise ToolError(f"{target} is not a file. fs.list the parent and retry.")
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"path": str(target), "bytes": len(text), "content": text}

    async def write_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, FsWriteParams):
            raise ToolError("internal: fs.write params mismatch")
        target = _inside(p.path, workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(p.content, encoding="utf-8")
        return {"written": str(target), "bytes": len(p.content)}

    async def list_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, FsListParams):
            raise ToolError("internal: fs.list params mismatch")
        target = _inside(p.path, workspace)
        if not target.is_dir():
            raise ToolError(f"{target} is not a directory.")
        names = sorted(x.name + ("/" if x.is_dir() else "") for x in target.iterdir())
        return {"path": str(target), "entries": names[:200]}

    return [
        Tool("fs.read", "Read a UTF-8 file inside the workspace.", FsReadParams, read_h, "T1"),
        Tool("fs.write", "Write a UTF-8 file inside the workspace.", FsWriteParams, write_h, "T1"),
        Tool("fs.list", "List a workspace directory.", FsListParams, list_h, "T1"),
    ]
