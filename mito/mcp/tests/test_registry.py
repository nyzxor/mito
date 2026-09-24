from __future__ import annotations

from pathlib import Path

import pytest
from mito.mcp.registry import McpError, McpRegistry

pytestmark = pytest.mark.unit

TOML = """
schema_version = 1

[[server]]
name = "example"
url = "https://mcp.example.invalid/mcp"
version = "1.0.0"
tools = ["search"]
"""


def test_unknown_server_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text(TOML, encoding="utf-8")
    reg = McpRegistry.load(path)
    with pytest.raises(McpError, match="not allowlisted"):
        reg.get("nope")


def test_allowlisted_call_fails_closed_and_labels_untrusted(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text(TOML, encoding="utf-8")
    reg = McpRegistry.load(path)
    with pytest.raises(McpError, match="UNTRUSTED"):
        reg.call("example", "search", {})
    with pytest.raises(McpError, match="not listed"):
        reg.call("example", "write", {})


def test_empty_file_is_empty_registry(tmp_path: Path) -> None:
    assert McpRegistry.load(tmp_path / "missing.toml").list() == []
