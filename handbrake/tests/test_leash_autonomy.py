"""DESIGN §5.2 leash and §11 autonomy ladder storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from handbrake.leash.leash import Leash
from handbrake.policy.autonomy import AutonomyStore

pytestmark = pytest.mark.handbrake


class Clock:
    def __init__(self) -> None:
        self.t = 1_700_000_000.0

    def __call__(self) -> float:
        return self.t


def test_autonomy_defaults_to_a1_and_demotes(tmp_path: Path) -> None:
    store = AutonomyStore(tmp_path / "autonomy.json")
    assert store.get() == "A1"
    store.set("A2", by="operator")
    assert store.get() == "A2"
    assert store.demote("leash expired") == "A1"
    assert store.demote("brake trip") == "A0"
    assert store.demote("again") == "A0"
    with pytest.raises(ValueError):
        store.set("A3", by="operator")


def test_dev_mode_caps_autonomy_at_a1(tmp_path: Path) -> None:
    store = AutonomyStore(tmp_path / "autonomy.json", dev_mode=True)
    store.set("A2", by="operator")
    assert store.get() == "A1"


def test_leash_checkin_and_expiry(tmp_path: Path) -> None:
    clock = Clock()
    leash = Leash(tmp_path / "leash.json", ttl_hours=72, clock=clock)
    assert leash.status().expired_periods == 0  # fresh leash starts at creation time
    leash.checkin("cli")
    clock.t += 71 * 3600
    st = leash.status()
    assert st.expired_periods == 0 and st.remaining_s > 0
    clock.t += 2 * 3600
    st = leash.status()
    assert st.expired_periods == 1
    clock.t += 72 * 3600
    assert leash.status().expired_periods == 2


def test_leash_apply_demotes_then_rests(tmp_path: Path) -> None:
    clock = Clock()
    leash = Leash(tmp_path / "leash.json", ttl_hours=72, clock=clock)
    store = AutonomyStore(tmp_path / "autonomy.json")
    store.set("A2", by="operator")
    actions: list[str] = []
    clock.t += 73 * 3600
    assert leash.apply(store, rest=lambda: actions.append("rest")) == ["demote:A1"]
    assert store.get() == "A1"
    assert leash.apply(store, rest=lambda: actions.append("rest")) == []  # idempotent
    clock.t += 72 * 3600
    assert leash.apply(store, rest=lambda: actions.append("rest")) == ["deep_rest"]
    assert actions == ["rest"]
    leash.checkin("telegram")
    assert leash.status().expired_periods == 0
    assert leash.apply(store, rest=lambda: actions.append("rest")) == []
