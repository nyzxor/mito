"""Unplugged-wire detection, part 3: every gate/meter/verifier wire has at least one caller
outside tests and outside its own definition (the guide's headline failure)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.wiring

# wire -> (regex that only matches a real call site, file that defines it)
WIRES: dict[str, tuple[str, str]] = {
    "Gate.dispatch": (r"\bgate\.dispatch\(", "mito/loop/gate.py"),
    "ModelGateway.call": (r"\bgateway\.call\(", "mito/gateway/gateway.py"),
    "PolicyEngine.evaluate": (r"\bpolicy\.evaluate\(", "handbrake/policy/engine.py"),
    "BudgetGovernor.preflight": (r"\bgovernor\.preflight\(", "handbrake/budget/governor.py"),
    "BudgetGovernor.reconcile": (r"\bgovernor\.reconcile\(", "handbrake/budget/governor.py"),
    "BudgetGovernor.check_progress": (
        r"\bgovernor\.check_progress\(",
        "handbrake/budget/governor.py",
    ),
    "AuditChain.append": (r"\baudit\.append\(", "handbrake/audit/chain.py"),
    "IntegrityChecker.check": (r"\bintegrity\.check\(", "handbrake/integrity/pins.py"),
    "Supervisor.poll": (r"\bsupervisor\.poll\(", "handbrake/kill/switch.py"),
    "Leash.apply": (r"\bleash\.apply\(", "handbrake/leash/leash.py"),
    "ApprovalStore.consume": (r"\bapprovals\.consume\(", "handbrake/approval/store.py"),
    "SignedCommand.verify": (r"\bcmd\.verify\(", "handbrake/integrity/commands.py"),
    "require_grant": (r"\brequire_grant\(", "mito/gateway/seal.py"),
    "require_dispatch": (r"\brequire_dispatch\(", "mito/tools/seal.py"),
    "Handbrake.record_result": (r"\.record_result\(", "handbrake/core.py"),
    "compact_result": (r"\bcompact_result\(", "mito/loop/compact.py"),
    "EgressGateway.request": (r"\begress\.request\(", "handbrake/egress/gateway.py"),
    "VaultStore.resolve": (r"\bvault\.resolve\(", "handbrake/vault/store.py"),
    "DockerSandbox.run": (r"\bsandbox\.run\(", "handbrake/sandbox/runner.py"),
    "tag_provenance": (r"\btag_provenance\(", "mito/loop/taint.py"),
    "Ledger.topup": (r"\bledger\.topup\(", "handbrake/ledger/book.py"),
    "Ledger.confirm_claim": (r"\bledger\.confirm_claim\(", "handbrake/ledger/book.py"),
    "MetabolismEngine.evaluate": (r"\bmetabolism\.evaluate\(", "handbrake/ledger/metabolism.py"),
    "Skill.granted_tools": (r"\.granted_tools\(", "mito/skills_rt/loader.py"),
    "McpRegistry.call": (r"\bregistry\.call\(", "mito/mcp/registry.py"),
    "MemoryStore.write": (r"\bstore\.write\(", "mito/memory/store.py"),
}


def _sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in ("handbrake", "mito"):
        for p in (ROOT / rel).rglob("*.py"):
            if "tests" in p.parts:
                continue
            out[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8")
    return out


def test_every_wire_has_a_caller() -> None:
    sources = _sources()
    missing: list[str] = []
    for wire, (pattern, definer) in WIRES.items():
        rx = re.compile(pattern)
        callers = [f for f, src in sources.items() if f != definer and rx.search(src)]
        if not callers:
            missing.append(wire)
    assert not missing, f"wires with zero callers outside tests: {missing}"
