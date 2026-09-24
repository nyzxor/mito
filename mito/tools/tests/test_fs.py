"""Workspace confinement is enforced in the fs handler, not only by policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from mito.tools.base import ToolError
from mito.tools.fs import _inside

pytestmark = pytest.mark.unit


def test_inside_accepts_workspace_child(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "a.txt"
    assert _inside(str(target), str(ws)) == target.resolve()


def test_inside_rejects_escape(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ToolError, match="outside"):
        _inside(str(tmp_path / "nope.txt"), str(ws))
