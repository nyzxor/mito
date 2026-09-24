"""Handbrake facade: init, gate flows (allow/ask/deny/simulate, approval consume, halted, frozen),
model metering, leash tick, integrity freeze. All in-process, no network."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from handbrake.core import GateRequest, Handbrake
from handbrake.kill.switch import HaltLevel
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo

pytestmark = pytest.mark.handbrake


@pytest.fixture
def hb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Handbrake:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    return Handbrake(paths, repo, dev_mode=True)


def req(
    tool: str, args: dict[str, object] | None = None, session: str = "s1", purpose: str = "test"
) -> GateRequest:
    return GateRequest(
        session=session,
        tool=tool,
        args=args or {},
        taint_flags=[],
        purpose=purpose,
        workspace="/work/s1",
    )


def test_init_creates_control_state(hb: Handbrake) -> None:
    c = hb.paths.control
    for name in (
        "tokens.json",
        "operator.pub",
        "pins.json",
        "anchor.key",
        "autonomy.json",
        "leash.json",
        "audit.jsonl",
    ):
        assert (c / name).exists(), name
    assert hb.integrity_check().ok
    st = hb.state()
    assert (
        st["autonomy"] == "A1"
        and st["dev_mode"] is True
        and st["halt"] is None
        and st["frozen"] is False
    )


def test_gate_allow_issues_ticket_and_records_result(hb: Handbrake) -> None:
    r = hb.gate_dispatch(req("time"))
    assert r.kind == "allow" and r.ticket is not None
    hb.record_result("s1", "time", r.ticket, ok=True, result_hash="abc", cost_atp=0)
    assert hb.audit.verify().ok
    kinds = [rec.kind for rec in hb.audit_tail(5)]
    assert "gate.verdict" in kinds and "tool.result" in kinds


def test_ticket_is_single_use(hb: Handbrake) -> None:
    r = hb.gate_dispatch(req("time"))
    assert r.ticket is not None
    hb.record_result("s1", "time", r.ticket, ok=True, result_hash="a", cost_atp=0)
    with pytest.raises(PermissionError):
        hb.record_result("s1", "time", r.ticket, ok=True, result_hash="a", cost_atp=0)


def test_gate_ask_creates_card_then_approval_allows_once(hb: Handbrake) -> None:
    r = hb.gate_dispatch(req("memory.write", {"name": "n", "body": "b"}))
    assert r.kind == "ask" and r.card is not None and r.ticket is None
    assert hb.pending_approvals()[0].action_hash == r.card.action_hash
    hb.approve(r.card.action_hash, by="operator:cli")
    r2 = hb.gate_dispatch(req("memory.write", {"name": "n", "body": "b"}))
    assert r2.kind == "allow" and r2.ticket is not None and "approved" in r2.reason
    r3 = hb.gate_dispatch(req("memory.write", {"name": "n", "body": "b"}))
    assert r3.kind == "ask"  # single use: a new card


def test_gate_deny_blacklist_and_unknown(hb: Handbrake) -> None:
    assert hb.gate_dispatch(req("payment.transfer")).kind == "deny"
    assert hb.gate_dispatch(req("nope")).kind == "deny"


def test_gate_denies_everything_when_halted(hb: Handbrake) -> None:
    hb.halt(HaltLevel.SOFT, source="test")
    r = hb.gate_dispatch(req("time"))
    assert r.kind == "deny" and "halt" in r.reason.lower()
    hb.wake(by="operator")
    assert hb.gate_dispatch(req("time")).kind == "allow"


def test_session_taint_is_monotonic_and_drives_trifecta(hb: Handbrake) -> None:
    hb.autonomy.set(
        "A2", by="test"
    )  # dev_mode caps at A1 -> still A1; emulate via direct ctx flags
    r = hb.gate_dispatch(
        GateRequest("s2", "email.read", {"folder": "INBOX"}, ["A", "B"], "read", "/w")
    )
    assert r.kind == "allow"
    assert r.ticket is not None
    hb.record_result("s2", "email.read", r.ticket, ok=True, result_hash="h", cost_atp=0)
    # runtime "forgets" its flags; the Handbrake remembers
    r2 = hb.gate_dispatch(GateRequest("s2", "notify.human", {"text": "x"}, [], "tell", "/w"))
    assert r2.kind == "ask" and "Rule of Two" in r2.reason


def test_no_progress_detector_denies_third_identical_call(hb: Handbrake) -> None:
    assert hb.gate_dispatch(req("time", {"tz": "UTC"})).kind == "allow"
    r2 = hb.gate_dispatch(req("time", {"tz": "UTC"}))
    assert r2.kind == "allow" and r2.hint and "identical" in r2.hint
    r3 = hb.gate_dispatch(req("time", {"tz": "UTC"}))
    assert r3.kind == "deny" and "progress" in r3.reason


def test_model_preflight_and_reconcile_are_audited(hb: Handbrake) -> None:
    grant = hb.model_preflight("s1", "t1", "L1", 0.001, "local-llamacpp")
    hb.model_reconcile(grant.meter_id, 0.0005, {"prompt_tokens": 10, "completion_tokens": 5})
    kinds = [rec.kind for rec in hb.audit_tail(3)]
    assert "model.preflight" in kinds and "model.reconcile" in kinds


def test_model_preflight_denied_over_cap(hb: Handbrake) -> None:
    from handbrake.budget.governor import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        hb.model_preflight("s1", "t1", "L2", 9.0, "m")


def test_integrity_mismatch_freezes_external_actions(hb: Handbrake) -> None:
    (hb.repo_root / "policy" / "policy.toml").write_text("tampered\n", encoding="utf-8")
    res = hb.integrity_check()
    assert not res.ok and hb.state()["frozen"] is True
    assert hb.gate_dispatch(req("time")).kind == "allow"  # T0 still fine
    assert hb.gate_dispatch(req("web.fetch", {"url": "https://x"})).kind == "deny"
    with pytest.raises(PermissionError):
        hb.model_preflight("s1", "t", "L2", 0.01, "m")


def test_signed_autonomy_set_and_replay(hb: Handbrake) -> None:
    from handbrake.integrity.commands import CommandError, SignedCommand

    key = hb.keystore.load_private()
    cmd = SignedCommand.build("autonomy.set", {"level": "A0"}, key)
    assert hb.autonomy_set(cmd.to_dict()) == "A0"
    with pytest.raises(CommandError):
        hb.autonomy_set(cmd.to_dict())


def test_tick_runs_supervisor_and_leash(hb: Handbrake) -> None:
    report = hb.tick()
    assert report["halt"] is None and report["leash_actions"] == []


def test_tokens_are_distinct_and_not_world_readable_content(hb: Handbrake) -> None:
    tokens = hb.tokens()
    assert tokens["runtime"] != tokens["operator"] and len(tokens["runtime"]) >= 32
    assert os.environ.get("MITO_OPERATOR_KEY_FILE")  # test isolation sanity
