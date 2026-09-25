"""Promotion gate (DESIGN §9.3). Deterministic. The author is not the judge."""

from __future__ import annotations

from dataclasses import dataclass

IMMUTABLE = ("handbrake/", "policy/", "evals/safety/", "config/metabolism.toml")
WRITABLE = ("skills/", "prompts/")
AUTO_TIERS = frozenset({"T0", "T1", "T2"})
BLOCKED_STATES = frozenset({"FRUGAL", "STARVING", "DEEP_REST"})
MAX_PER_DAY = 3


@dataclass(frozen=True)
class Proposal:
    path: str
    tier: str
    rationale: str
    body: str


@dataclass(frozen=True)
class Admit:
    kind: str  # allow | ask | deny
    reason: str


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


class PromotionGate:
    def admit(self, proposal: Proposal, *, today_count: int, state: str) -> Admit:
        path = _norm(proposal.path)
        if state in BLOCKED_STATES:
            return Admit("deny", f"{state} blocks evolution")
        if any(path == p.rstrip("/") or path.startswith(p) for p in IMMUTABLE):
            return Admit("deny", f"{path} is immutable to evolution")
        if not any(path.startswith(p) for p in WRITABLE):
            return Admit("deny", "evolution may only write skills/ or prompts/")
        if ".." in path.split("/"):
            return Admit("deny", "path escape")
        if proposal.tier not in AUTO_TIERS:
            return Admit("ask", f"{proposal.tier} needs operator approval; not applied")
        if today_count >= MAX_PER_DAY:
            return Admit("deny", f"daily cap {MAX_PER_DAY} reached")
        if not proposal.rationale.strip():
            return Admit("deny", "rationale is required")
        return Admit("allow", "T0-T2 within the daily cap")
