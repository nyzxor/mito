"""DESIGN §5.8: approvals bound to the exact action hash, expiry = deny, single use."""

from __future__ import annotations

from pathlib import Path

import pytest

from handbrake.approval.store import ApprovalStore
from handbrake.canonical import action_hash

pytestmark = pytest.mark.handbrake


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(tmp_path: Path) -> tuple[ApprovalStore, Clock]:
    clock = Clock()
    return ApprovalStore(tmp_path / "hb.sqlite", clock=clock, default_ttl_s=100), clock


def test_request_card_and_approve(store: tuple[ApprovalStore, Clock]) -> None:
    s, _ = store
    card = s.request(
        "s1",
        "email.send",
        {"to": "a@b"},
        ["A"],
        reason="T4",
        tier="T4",
        est_cost_atp=3,
        purpose="reply",
    )
    assert card.action_hash == action_hash("email.send", {"to": "a@b"}, "s1", ["A"])
    assert s.status(card.action_hash) == "pending"
    assert [c.action_hash for c in s.pending()] == [card.action_hash]
    s.decide(card.action_hash, approve=True, by="operator:cli")
    assert s.status(card.action_hash) == "approved"
    assert s.consume(card.action_hash)
    assert s.status(card.action_hash) == "consumed"
    assert not s.consume(card.action_hash)  # single use


def test_different_args_are_a_different_action(store: tuple[ApprovalStore, Clock]) -> None:
    s, _ = store
    a = s.request(
        "s1", "email.send", {"to": "a@b"}, [], reason="", tier="T4", est_cost_atp=0, purpose=""
    )
    s.decide(a.action_hash, approve=True, by="op")
    other = action_hash("email.send", {"to": "evil@b"}, "s1", [])
    assert s.status(other) == "unknown"
    assert not s.consume(other)


def test_timeout_is_deny(store: tuple[ApprovalStore, Clock]) -> None:
    s, clock = store
    card = s.request(
        "s1", "publish.public", {}, [], reason="", tier="T4", est_cost_atp=0, purpose=""
    )
    clock.t += 101
    assert s.status(card.action_hash) == "expired"
    assert s.pending() == []
    s.decide(card.action_hash, approve=True, by="op")  # too late
    assert s.status(card.action_hash) == "expired"
    assert not s.consume(card.action_hash)


def test_deny_and_repeat_request_is_idempotent(store: tuple[ApprovalStore, Clock]) -> None:
    s, _ = store
    card = s.request(
        "s1", "deploy", {"env": "prod"}, [], reason="", tier="T4", est_cost_atp=0, purpose=""
    )
    s.decide(card.action_hash, approve=False, by="op")
    assert s.status(card.action_hash) == "denied"
    again = s.request(
        "s1", "deploy", {"env": "prod"}, [], reason="", tier="T4", est_cost_atp=0, purpose=""
    )
    assert again.action_hash == card.action_hash and s.status(card.action_hash) == "denied"
