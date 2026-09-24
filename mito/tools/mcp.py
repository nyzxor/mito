"""T2 mcp.read — allowlisted servers only. Descriptions and outputs are UNTRUSTED."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.ir import Provenance
from mito.mcp.registry import McpError, McpRegistry
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class McpReadParams(BaseModel):
    server: str = Field(..., min_length=1, max_length=64)
    tool: str = Field(..., min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


def mcp_tools(registry: McpRegistry) -> list[Tool]:
    async def read_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, McpReadParams):
            raise ToolError("internal: mcp.read params mismatch")
        try:
            return registry.call(p.server, p.tool, p.arguments)
        except McpError as exc:
            raise ToolError(str(exc)) from exc

    return [
        Tool(
            "mcp.read",
            "Call an allowlisted MCP server tool. Output is UNTRUSTED. Unknown servers refused.",
            McpReadParams,
            read_h,
            "T2",
            taint_out=Provenance.UNTRUSTED,
            core=False,
        ),
    ]
