from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from handbrake.tests.conftest import make_repo

from mito.cli import main as cli

pytestmark = pytest.mark.unit

REQUIRED_SURFACE = {
    "status",
    "halt",
    "rest",
    "wake",
    "approve",
    "deny",
    "ledger",
    "autonomy",
    "audit",
    "evolve",
    "skills",
    "memory",
    "policy",
    "vault",
    "checkin",
    "up",
    "init",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = make_repo(tmp_path / "repo")
    monkeypatch.setenv("MITO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    monkeypatch.setenv("MITO_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    monkeypatch.setenv("MITO_HANDBRAKE_BIND", "127.0.0.1:1")  # nothing listens: CLI goes in-process
    monkeypatch.setattr(cli, "repo_root", lambda: repo)
    import mito.cli.operator as op

    monkeypatch.setattr(op, "repo_root", lambda: repo)
    return repo


def test_minimum_cli_surface_is_registered() -> None:
    assert REQUIRED_SURFACE <= set(cli._COMMANDS)


def test_every_command_parses() -> None:
    parser = cli.build_parser()
    for name in cli._COMMANDS:
        assert parser.parse_args([name]).command == name


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "MITO operator CLI" in capsys.readouterr().out


def test_uninitialized_home_fails_cleanly(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status"]) == 2
    assert "mito init" in capsys.readouterr().err


def test_init_status_halt_wake_roundtrip(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["--json", "status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["autonomy"] == "A1" and st["halt"] is None
    assert cli.main(["halt", "--hard"]) == 0
    capsys.readouterr()
    assert cli.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["halt"]["level"] == "hard"
    assert cli.main(["wake"]) == 0
    capsys.readouterr()
    assert cli.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["halt"] is None


def test_audit_verify_and_tail(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["init"])
    capsys.readouterr()
    assert cli.main(["audit", "verify"]) == 0
    assert "OK" in capsys.readouterr().out
    assert cli.main(["audit", "tail", "3"]) == 0
    assert "init" in capsys.readouterr().out


def test_policy_sign_after_edit_restores_integrity(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init"])
    (env / "policy" / "policy.toml").write_text(
        "schema_version = 1\n[defaults]\nT0='allow'\n[autonomy]\nA0=[]\nA1=['T0']\nA2=['T0']\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert (
        cli.main(["--json", "status"]) == 0
    )  # constructing the Handbrake does not freeze by itself
    assert cli.main(["policy", "sign"]) == 0
    assert "integrity now: ok" in capsys.readouterr().out


def test_autonomy_set_is_signed_and_capped_in_dev_mode(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init"])
    capsys.readouterr()
    assert cli.main(["autonomy", "set", "A0"]) == 0
    assert "A0 (signed)" in capsys.readouterr().out
    assert cli.main(["autonomy", "set", "A3"]) == 2


def test_approve_lists_and_resolves_prefix(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from handbrake.core import Handbrake
    from handbrake.paths import MitoPaths

    cli.main(["init"])
    hb = Handbrake(MitoPaths.from_env(), env)
    card = hb.approvals.request(
        "s",
        "memory.write",
        {"name": "n"},
        [],
        reason="T3",
        tier="T3",
        est_cost_atp=0,
        purpose="test",
    )
    capsys.readouterr()
    assert cli.main(["approve"]) == 0
    assert card.action_hash[:12] in capsys.readouterr().out
    assert cli.main(["approve", card.action_hash[:8]]) == 0
    assert "approved" in capsys.readouterr().out
    assert hb.approvals.status(card.action_hash) == "approved"


def test_not_yet_commands_fail_loudly(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["evolve", "review"]) == 2
    assert "Phase 6" in capsys.readouterr().err


def test_skills_quarantine_list_and_approve(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill = """---
name: draft
description: A quarantined skill.
version: 0.1.0
mito:
  tools_required: [memory.search]
  risk_tier: T0
  origin: quarantine
---
## Procedure
Search.
"""
    dest = env / "skills" / ".quarantine" / "draft" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text(skill, encoding="utf-8")
    assert cli.main(["skills", "quarantine", "list"]) == 0
    assert "draft" in capsys.readouterr().out
    assert cli.main(["skills", "quarantine", "approve", "draft"]) == 0
    assert (env / "skills" / "draft" / "SKILL.md").is_file()
    capsys.readouterr()
    assert cli.main(["skills", "quarantine", "list"]) == 0
    # source file remains in quarantine; list still sees it
    assert "draft" in capsys.readouterr().out


def test_memory_confirm_lifts_quarantine(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from handbrake.paths import MitoPaths

    from mito.memory.store import MemoryStore

    cli.main(["init"])
    store = MemoryStore(MitoPaths.from_env().runtime / "memory")
    store.write("note", "hello", source="agent", run_id="s", flags=["A"])
    capsys.readouterr()
    assert cli.main(["memory", "confirm", "note"]) == 0
    assert "operator-confirmed" in capsys.readouterr().out
    assert store.get("note") is not None



def test_ledger_topup_and_rest(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["ledger", "topup", "500"]) == 0
    assert "500" in capsys.readouterr().out
    assert cli.main(["rest"]) == 0
    assert "Deep Rest" in capsys.readouterr().out


def test_vault_add_list_hides_secret(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setenv("MITO_VAULT_KEY_FILE", str(env.parent / "vault.key"))
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO("super-secret-value\n"))
    assert cli.main(["vault", "add", "github-readonly"]) == 0
    out = capsys.readouterr().out
    assert "cred:github-readonly" in out and "super-secret-value" not in out
    assert cli.main(["--json", "vault", "list"]) == 0
    listed = capsys.readouterr().out
    assert "cred:github-readonly" in listed and "super-secret-value" not in listed
