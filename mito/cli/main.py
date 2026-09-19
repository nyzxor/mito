"""MITO operator CLI (ADR-0013).

Phase 0: scaffold only. Subcommands are registered so `uv run mito --help` documents the
surface; every command reports that it is not implemented yet. Phase 1 wires `status`,
`halt`, `audit verify`, `policy sign`, `autonomy set`, `up`, `init`.
"""

from __future__ import annotations

import argparse
import sys

from mito import __version__

PHASE = 0

_COMMANDS: dict[str, str] = {
    "status": "state, balance, runway, burn, pending approvals, autonomy, last audit verify",
    "halt": "stop: --soft (finish step, rest) | --hard (2 s) | --panic (+revoke, close egress)",
    "rest": "enter Deep Rest",
    "wake": "leave Deep Rest (requires balance >= wake threshold or operator override)",
    "approve": "approve a pending action by id (bound to its action hash)",
    "deny": "deny a pending action by id",
    "ledger": "ledger confirm <id> — mark an income claim as verified",
    "autonomy": "autonomy set <A0|A1|A2> — signed operator command",
    "audit": "audit verify — recompute the hash chain and anchors",
    "evolve": "evolve review — list evolution commits awaiting review",
    "skills": "skills quarantine list|approve <name>",
    "policy": "policy sign — re-pin policy/ and budgets after an operator edit",
    "vault": "vault add <handle> — store a credential behind a handle",
    "checkin": "operator check-in (resets the leash)",
    "up": "start the Handbrake, which starts the runtime",
    "init": "first-run: create state dir, operator key, pins",
    "dev": "developer tasks used by the justfile (clean, sandbox-build, evals-record)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mito", description="MITO operator CLI")
    parser.add_argument(
        "--version", action="version", version=f"mito {__version__} (phase {PHASE})"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in _COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    print(f"mito {ns.command}: not implemented in phase {PHASE} (design only).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
