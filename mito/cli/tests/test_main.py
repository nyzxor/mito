from __future__ import annotations

import pytest

from mito.cli.main import _COMMANDS, build_parser, main

pytestmark = pytest.mark.unit

REQUIRED_SURFACE = {
    "status", "halt", "rest", "wake", "approve", "deny", "ledger", "autonomy",
    "audit", "evolve", "skills",
}  # fmt: skip


def test_minimum_cli_surface_is_registered() -> None:
    assert REQUIRED_SURFACE <= set(_COMMANDS)


def test_every_command_parses() -> None:
    parser = build_parser()
    for name in _COMMANDS:
        ns = parser.parse_args([name])
        assert ns.command == name


def test_no_command_prints_help_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "MITO operator CLI" in capsys.readouterr().out


def test_unimplemented_command_fails_loudly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 2
    assert "not implemented" in capsys.readouterr().err
