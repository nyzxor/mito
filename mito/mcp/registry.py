"""MCP allowlist (DESIGN §8). Unknown servers are refused. No auto-install."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class McpError(ValueError):
    pass


@dataclass(frozen=True)
class McpServer:
    name: str
    url: str
    version: str
    tools: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "version": self.version,
            "tools": list(self.tools),
            "taint": "UNTRUSTED",
        }


class McpRegistry:
    def __init__(self, servers: list[McpServer]) -> None:
        self._by_name = {s.name: s for s in servers}

    @classmethod
    def load(cls, path: Path) -> McpRegistry:
        if not path.is_file():
            return cls([])
        with path.open("rb") as f:
            data = tomllib.load(f)
        if int(data.get("schema_version", 1)) != 1:
            raise McpError(f"{path}: unsupported schema_version {data.get('schema_version')}")
        servers: list[McpServer] = []
        for raw in data.get("server", []):
            servers.append(
                McpServer(
                    name=str(raw["name"]),
                    url=str(raw["url"]),
                    version=str(raw["version"]),
                    tools=tuple(str(t) for t in raw.get("tools", [])),
                )
            )
        return cls(servers)

    def get(self, name: str) -> McpServer:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise McpError(
                f"MCP server {name!r} is not allowlisted; ask the operator to edit config/mcp.toml"
            ) from exc

    def list(self) -> list[McpServer]:
        return [self._by_name[k] for k in sorted(self._by_name)]

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Phase 4: allowlist + refuse. Live JSON-RPC waits for a pinned transport."""
        spec = self.get(server)
        if spec.tools and tool not in spec.tools:
            raise McpError(f"tool {tool!r} is not listed for MCP server {server}")
        raise McpError(
            f"MCP server {server} ({spec.version}) is allowlisted but has no live session; "
            "this call is refused fail-closed. Output would be UNTRUSTED."
        )
