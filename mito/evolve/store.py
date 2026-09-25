"""Proposal inbox under $MITO_HOME/runtime/evolve. Review re-judges; it does not trust the file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mito.evolve.gate import Admit, PromotionGate, Proposal


def inbox(root: Path) -> Path:
    path = root / "inbox.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append(root: Path, proposal: Proposal) -> None:
    with inbox(root).open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "path": proposal.path,
                    "tier": proposal.tier,
                    "rationale": proposal.rationale,
                    "body": proposal.body,
                }
            )
            + "\n"
        )


def review(root: Path, *, state: str, today_count: int) -> list[dict[str, Any]]:
    gate = PromotionGate()
    path = inbox(root)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        proposal = Proposal(
            str(raw["path"]), str(raw["tier"]), str(raw["rationale"]), str(raw.get("body", ""))
        )
        verdict: Admit = gate.admit(proposal, today_count=today_count, state=state)
        out.append(
            {
                "path": proposal.path,
                "tier": proposal.tier,
                "kind": verdict.kind,
                "reason": verdict.reason,
            }
        )
    return out


def apply_allowed(root: Path, repo: Path, proposal: Proposal, verdict: Admit) -> Path | None:
    if verdict.kind != "allow":
        return None
    dest = repo / proposal.path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(proposal.body, encoding="utf-8")
    applied = root / "applied.jsonl"
    with applied.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"path": proposal.path, "tier": proposal.tier}) + "\n")
    return dest
