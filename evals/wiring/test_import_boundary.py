"""Unplugged-wire detection, part 1 (DESIGN §5.11): import boundaries enforced by AST.

- `handbrake` never imports `mito`.
- In `mito/`, raw network/process primitives and provider SDKs are confined to two modules.
- The meter seal `_SEAL` is imported only by `mito.gateway.gateway`; the gate seal `_GATE_SEAL`
  only by `mito.loop.gate`. Everything else must go through ModelGateway.call / Gate.dispatch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.wiring

ALLOWED_IN_MITO: dict[str, set[str]] = {
    "httpx": {
        "mito/gateway/client.py",
        "mito/gateway/handbrake_client.py",
        "mito/cli/operator.py",  # operator channel to the Handbrake, never a model/tool path
    },
    "socket": set(),
    "subprocess": set(),  # sandbox execution lives in the Handbrake (ADR-0006)
    "docker": set(),
    "openai": set(),
    "anthropic": set(),
    "litellm": set(),
    "requests": set(),
    "urllib": set(),
    "aiohttp": set(),
}

SEALS: dict[tuple[str, str], set[str]] = {
    ("mito.gateway.seal", "_SEAL"): {"mito/gateway/gateway.py"},
    ("mito.tools.seal", "_GATE_SEAL"): {"mito/loop/gate.py"},
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _from_imports(path: Path) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.update((node.module, alias.name) for alias in node.names)
    return out


def _py_files(rel: str, *, include_tests: bool = False) -> list[Path]:
    return [p for p in (ROOT / rel).rglob("*.py") if include_tests or "tests" not in p.parts]


def test_handbrake_never_imports_mito() -> None:
    offenders = [p for p in _py_files("handbrake", include_tests=True) if "mito" in _imports(p)]
    assert not offenders, f"handbrake must not import mito: {offenders}"


def test_mito_network_and_process_primitives_are_confined() -> None:
    offenders: list[str] = []
    for p in _py_files("mito"):
        rel = p.relative_to(ROOT).as_posix()
        for mod, allowed in ALLOWED_IN_MITO.items():
            if mod in _imports(p) and rel not in allowed:
                offenders.append(f"{rel} imports {mod}")
    assert not offenders, "\n".join(offenders)


def test_seals_are_imported_only_by_their_owners() -> None:
    offenders: list[str] = []
    for p in [*_py_files("mito", include_tests=True), *_py_files("evals", include_tests=True)]:
        rel = p.relative_to(ROOT).as_posix()
        for seal, owners in SEALS.items():
            if seal in _from_imports(p) and rel not in owners:
                offenders.append(f"{rel} imports {seal[1]} from {seal[0]}")
    assert not offenders, "\n".join(offenders)


def test_only_gate_constructs_dispatch_and_only_gateway_constructs_grants() -> None:
    """Belt and braces: even without the seal names, nobody else instantiates the envelopes."""
    offenders: list[str] = []
    for p in _py_files("mito"):
        rel = p.relative_to(ROOT).as_posix()
        for node in ast.walk(_tree(p)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "MeterGrant" and rel != "mito/gateway/gateway.py":
                    offenders.append(f"{rel} constructs MeterGrant")
                if node.func.id == "Dispatch" and rel != "mito/loop/gate.py":
                    offenders.append(f"{rel} constructs Dispatch")
    assert not offenders, "\n".join(offenders)
