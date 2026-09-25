from __future__ import annotations

from pathlib import Path

import pytest
from mito.evolve.gate import PromotionGate, Proposal
from mito.evolve.store import append, apply_allowed, review

pytestmark = pytest.mark.unit

GATE = PromotionGate()


def test_immutable_trees_are_denied() -> None:
    proposal = Proposal("handbrake/core.py", "T0", "because", "x = 1\n")
    verdict = GATE.admit(proposal, today_count=0, state="THRIVING")
    assert verdict.kind == "deny" and "immutable" in verdict.reason


def test_t3_is_ask_and_not_applied(tmp_path: Path) -> None:
    proposal = Proposal("skills/pay/SKILL.md", "T3", "send mail", "body\n")
    verdict = GATE.admit(proposal, today_count=0, state="NORMAL")
    assert verdict.kind == "ask"
    assert apply_allowed(tmp_path, tmp_path / "repo", proposal, verdict) is None


def test_t0_applies_under_skills_until_daily_cap(tmp_path: Path) -> None:
    proposal = Proposal("skills/note/SKILL.md", "T0", "repeated success", "---\n")
    ok = GATE.admit(proposal, today_count=2, state="THRIVING")
    dest = apply_allowed(tmp_path, tmp_path / "repo", proposal, ok)
    assert dest is not None and dest.is_file()
    capped = GATE.admit(proposal, today_count=3, state="THRIVING")
    assert capped.kind == "deny" and "cap" in capped.reason


def test_frugal_blocks_evolution() -> None:
    proposal = Proposal("prompts/x.md", "T1", "trim", "hi\n")
    assert GATE.admit(proposal, today_count=0, state="FRUGAL").kind == "deny"


def test_review_rejudges_inbox(tmp_path: Path) -> None:
    append(tmp_path, Proposal("policy/policy.toml", "T0", "raise budget", "x\n"))
    rows = review(tmp_path, state="THRIVING", today_count=0)
    assert rows[0]["kind"] == "deny"
