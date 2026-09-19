"""Unplugged-wire detection (DESIGN §5.11), Phase 0 skeleton.

Phase 1 extends this with the canary tool/model. For now it enforces the two import boundaries
that exist already: `handbrake` never imports `mito`, and `mito` never imports raw network/
process primitives outside the allowlisted modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.wiring

# Modules in `mito/` allowed to touch these primitives. Everything else must go through them.
ALLOWED_IN_MITO: dict[str, set[str]] = {
    "httpx": {"mito/gateway/client.py"},
    "socket": set(),
    "subprocess": set(),  # sandbox execution lives in the Handbrake (ADR-0006)
    "docker": set(),
    "openai": set(),
    "anthropic": set(),
    "litellm": set(),
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _py_files(rel: str) -> list[Path]:
    return [p for p in (ROOT / rel).rglob("*.py") if "tests" not in p.parts]


def test_handbrake_never_imports_mito() -> None:
    offenders = [p for p in _py_files("handbrake") if "mito" in _imports(p)]
    assert not offenders, f"handbrake must not import mito: {offenders}"


def test_mito_network_and_process_primitives_are_confined() -> None:
    offenders: list[str] = []
    for p in _py_files("mito"):
        rel = p.relative_to(ROOT).as_posix()
        for mod, allowed in ALLOWED_IN_MITO.items():
            if mod in _imports(p) and rel not in allowed:
                offenders.append(f"{rel} imports {mod}")
    assert not offenders, "\n".join(offenders)
