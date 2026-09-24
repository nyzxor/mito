from __future__ import annotations

from pathlib import Path

import pytest
from handbrake.paths import repo_root
from handbrake.schedule.intervals import load_pulse
from mito.pulse.engine import Pulse, Signal, signals_from_state

pytestmark = pytest.mark.unit


def test_no_signal_skips_and_deep_rest_never_turns() -> None:
    pulse = Pulse({"base_interval_s": 900.0, "frugal_multiplier": 2.0, "starving_multiplier": 4.0})
    skip = pulse.decide(state="NORMAL", signals=[], now=0)
    assert skip.action == "skip"
    rest = pulse.decide(state="DEEP_REST", signals=[Signal("mail", "mail:0")], now=1)
    assert rest.action == "rest" and "model" in rest.reason


def test_signal_turns_once_then_dedupes() -> None:
    pulse = Pulse(
        {"base_interval_s": 100.0, "frugal_multiplier": 2.0, "starving_multiplier": 4.0}
    )
    sig = [Signal("approvals", "approvals:1")]
    first = pulse.decide(state="NORMAL", signals=sig, now=0)
    second = pulse.decide(state="NORMAL", signals=sig, now=50)
    assert first.action == "turn" and second.action == "skip"


def test_mail_bomb_is_capped() -> None:
    pulse = Pulse(
        {"base_interval_s": 100.0, "frugal_multiplier": 2.0, "starving_multiplier": 4.0}
    )
    sigs = signals_from_state({"mail_unseen": 40, "pending_approvals": 0})
    decision = pulse.decide(state="FRUGAL", signals=sigs, now=0)
    assert decision.action == "turn"
    assert decision.reason.count("mail") == 5
    assert decision.interval_s == 200


def test_repo_metabolism_pulse_fields_load() -> None:
    cfg = load_pulse(repo_root() / "config" / "metabolism.toml")
    assert cfg["base_interval_s"] == 900
    assert Path("config/metabolism.toml")
