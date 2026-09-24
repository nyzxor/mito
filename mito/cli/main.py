"""MITO operator CLI (ADR-0013). Plain argparse, `--json` on every command.

Phase 1 implements: init, up, run, status, halt, wake, approve, deny, checkin, audit verify|tail,
policy sign, autonomy set, dev clean. Later phases: rest, ledger, evolve, skills, vault.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from handbrake.kill.switch import HaltLevel
from handbrake.paths import MitoPaths, detect_dev_mode, repo_root

from mito import __version__

PHASE = 1

NOT_YET: dict[str, str] = {
    "rest": "Phase 3 (metabolism)",
    "ledger": "Phase 3 (metabolism)",
    "evolve": "Phase 6 (evolution)",
    "skills": "Phase 4 (skills)",
    "vault": "Phase 2 (vault/broker)",
}


def _out(ns: argparse.Namespace, data: Any, text: str | None = None) -> None:
    if ns.json:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    else:
        print(
            text
            if text is not None
            else json.dumps(data, indent=1, default=str, ensure_ascii=False)
        )


def _paths() -> MitoPaths:
    return MitoPaths.from_env()


# ---- commands -----------------------------------------------------------------------------------
def cmd_init(ns: argparse.Namespace) -> int:
    from handbrake.core import Handbrake

    paths = _paths()
    hb = Handbrake.init(paths, repo_root())
    st = hb.state()
    _out(
        ns,
        st,
        (
            f"initialized {paths.home}\n"
            f"  dev_mode={st['dev_mode']}  autonomy={st['autonomy']}\n"
            "  operator key: keyring (or MITO_OPERATOR_KEY_FILE); pins signed"
        ),
    )
    if st["dev_mode"]:
        print(
            "WARNING: DEV MODE — weaker process isolation; autonomy capped at A1", file=sys.stderr
        )
    return 0


def cmd_up(ns: argparse.Namespace) -> int:
    from handbrake.api.server import run_handbrake

    paths = _paths()
    if not (paths.control / "tokens.json").exists():
        print("not initialized; run `mito init` first", file=sys.stderr)
        return 2
    bind = (
        ns.args[0]
        if ns.args
        else __import__("os").environ.get("MITO_HANDBRAKE_BIND", "127.0.0.1:8710")
    )
    print(f"[mito] Handbrake on http://{bind}  (Ctrl+C = soft halt)", file=sys.stderr)
    try:
        reason = asyncio.run(
            run_handbrake(paths, repo_root(), bind=bind, start_runtime=not ns.no_runtime)
        )
    except KeyboardInterrupt:
        reason = "interrupted"
    print(f"[mito] Handbrake stopped: {reason}", file=sys.stderr)
    return 0


def cmd_run(ns: argparse.Namespace) -> int:
    """Drive ONE turn against the running Handbrake with the configured local model."""
    from mito.cli.run import run_once

    if not ns.args:
        print("usage: mito run <task text>", file=sys.stderr)
        return 2
    return asyncio.run(run_once(_paths(), " ".join(ns.args), json_out=ns.json))


def cmd_status(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    st = connect(_paths()).state()
    halt = st["halt"]["level"] if st["halt"] else "-"
    lines = [
        (
            f"autonomy={st['autonomy']}  state={st['metabolic_state']}  "
            f"halt={halt}  frozen={st['frozen']}  dev_mode={st['dev_mode']}"
        ),
        (
            f"leash: {st['leash']['remaining_s'] / 3600:.1f}h left "
            f"(last: {st['leash']['last_source']})"
        ),
        (
            f"spend today (cloud): ${st['spend_usd']['day_cloud']:.4f}  "
            f"month: ${st['spend_usd']['month_cloud']:.2f}  "
            f"all today: ${st['spend_usd']['day_all']:.4f}"
        ),
        f"pending approvals: {st['pending_approvals']}   audit seq: {st['audit']['seq']}",
        f"integrity: {st['integrity']['reason'] if st['integrity'] else 'not checked yet'}",
    ]
    _out(ns, st, "\n".join(lines))
    return 0


def cmd_halt(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    level = HaltLevel.PANIC if ns.panic else HaltLevel.HARD if ns.hard else HaltLevel.SOFT
    st = connect(_paths()).halt(level)
    _out(ns, st, f"halt {level.value} requested; sentinel written. `mito wake` to resume.")
    return 0


def cmd_wake(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    ok = connect(_paths()).wake()
    _out(
        ns,
        {"ok": ok},
        "woke: HALT cleared, leash reset"
        if ok
        else (
            "cannot wake: integrity freeze active "
            "(run `mito policy sign` after reviewing the change)"
        ),
    )
    return 0 if ok else 1


def _resolve(prefix: str, pending: list[dict[str, Any]]) -> str:
    matches = [c["action_hash"] for c in pending if str(c["action_hash"]).startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(
            f"{len(matches)} pending approvals match {prefix!r}; "
            "use `mito status --json` or a longer prefix"
        )
    return str(matches[0])


def cmd_approve(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    op = connect(_paths())
    if not ns.args:
        cards = op.pending()
        _out(ns, cards, "\n\n".join(_render_card(c) for c in cards) or "no pending approvals")
        return 0
    status = op.approve(_resolve(ns.args[0], op.pending()))
    _out(ns, {"status": status}, f"approval: {status}")
    return 0


def cmd_deny(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    op = connect(_paths())
    if not ns.args:
        print("usage: mito deny <action-hash-prefix>", file=sys.stderr)
        return 2
    status = op.deny(_resolve(ns.args[0], op.pending()))
    _out(ns, {"status": status}, f"approval: {status}")
    return 0


def _render_card(c: dict[str, Any]) -> str:
    return (
        f"[{str(c['action_hash'])[:12]}] {c['tool']}  tier={c['tier']}\n"
        f"  why: {c.get('purpose') or '-'}\n  gate: {c.get('reason')}\n"
        f"  args: {json.dumps(c.get('args'), ensure_ascii=False)[:400]}\n"
        f"  taint: {'+'.join(c.get('taint', [])) or '-'}"
    )


def cmd_checkin(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    connect(_paths()).checkin()
    _out(ns, {"ok": True}, "checked in; leash reset")
    return 0


def cmd_audit(ns: argparse.Namespace) -> int:
    from handbrake.core import Handbrake

    sub = ns.args[0] if ns.args else "verify"
    paths = _paths()
    if sub == "verify":
        hb = Handbrake(paths, repo_root())
        res = hb.audit.verify()
        _out(
            ns,
            res.__dict__,
            (
                f"audit chain: {'OK' if res.ok else 'BROKEN'} — "
                f"{res.records} records; last {res.last_hash[:16]}"
            )
            + ("" if res.ok else f"\n  first bad seq {res.first_bad_seq}: {res.reason}"),
        )
        return 0 if res.ok else 1
    if sub == "tail":
        from mito.cli.operator import connect

        n = int(ns.args[1]) if len(ns.args) > 1 else 20
        recs = connect(paths).audit_tail(n)
        _out(
            ns,
            recs,
            "\n".join(
                f"{r['seq']:>6} {r['kind']:<20} "
                f"{json.dumps(r['payload'], ensure_ascii=False)[:120]}"
                for r in recs
            ),
        )
        return 0
    print("usage: mito audit verify | tail [n]", file=sys.stderr)
    return 2


def cmd_policy(ns: argparse.Namespace) -> int:
    from handbrake.core import PINNED_PATHS
    from handbrake.integrity.pins import hash_tree, sign_pins
    from handbrake.vault.keys import KeyStore

    from mito.cli.operator import connect

    if not ns.args or ns.args[0] != "sign":
        print("usage: mito policy sign", file=sys.stderr)
        return 2
    paths = _paths()
    key = KeyStore(paths.control / "operator.pub").load_private()
    hashes = hash_tree(repo_root(), PINNED_PATHS)
    sign_pins(hashes, key).save(paths.control / "pins.json")
    res = connect(paths).integrity()
    _out(ns, res, f"pinned {len(hashes)} files; integrity now: {res['reason']}")
    return 0 if res["ok"] else 1


def cmd_autonomy(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect, sign_command

    if len(ns.args) != 2 or ns.args[0] != "set" or ns.args[1] not in ("A0", "A1", "A2"):
        print("usage: mito autonomy set <A0|A1|A2>", file=sys.stderr)
        return 2
    paths = _paths()
    if ns.args[1] == "A2" and detect_dev_mode():
        print("DEV MODE caps autonomy at A1; A2 requires the container deployment", file=sys.stderr)
    level = connect(paths).autonomy_set(sign_command(paths, "autonomy.set", {"level": ns.args[1]}))
    _out(ns, {"level": level}, f"autonomy: {level} (signed)")
    return 0


def cmd_dev(ns: argparse.Namespace) -> int:
    task = ns.args[0] if ns.args else ""
    if task == "clean":
        for d in (".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"):
            shutil.rmtree(repo_root() / d, ignore_errors=True)
        for p in repo_root().rglob("__pycache__"):
            shutil.rmtree(p, ignore_errors=True)
        print("cleaned caches")
        return 0
    if task in ("sandbox-build", "evals-record"):
        print(
            f"mito dev {task}: arrives in {'Phase 2' if task == 'sandbox-build' else 'Phase 3'}",
            file=sys.stderr,
        )
        return 2
    print("usage: mito dev clean|sandbox-build|evals-record", file=sys.stderr)
    return 2


def cmd_not_yet(ns: argparse.Namespace) -> int:
    print(f"mito {ns.command}: not implemented yet — {NOT_YET[ns.command]}", file=sys.stderr)
    return 2


_COMMANDS: dict[str, tuple[str, Any]] = {
    "init": ("first-run: create $MITO_HOME, tokens, operator key, signed pins", cmd_init),
    "up": ("start the Handbrake (API + supervisor), which starts the runtime  [bind]", cmd_up),
    "run": (
        "drive one agent turn on the local model through the running Handbrake: run <task>",
        cmd_run,
    ),
    "status": (
        "state, autonomy, halt, leash, spend, pending approvals, audit, integrity",
        cmd_status,
    ),
    "halt": ("stop: --soft (default) | --hard (2 s) | --panic (+revoke, close egress)", cmd_halt),
    "wake": ("clear the HALT sentinel and reset the leash", cmd_wake),
    "approve": (
        "list pending cards, or approve <hash-prefix> (bound to the exact action)",
        cmd_approve,
    ),
    "deny": ("deny <hash-prefix>", cmd_deny),
    "checkin": ("operator check-in (resets the 72 h leash)", cmd_checkin),
    "audit": ("audit verify | tail [n]", cmd_audit),
    "policy": (
        "policy sign — re-pin handbrake/, policy/, evals/safety/, budgets after an operator edit",
        cmd_policy,
    ),
    "autonomy": ("autonomy set <A0|A1|A2> — signed operator command", cmd_autonomy),
    "rest": ("enter Deep Rest", cmd_not_yet),
    "ledger": ("ledger confirm <id>", cmd_not_yet),
    "evolve": ("evolve review", cmd_not_yet),
    "skills": ("skills quarantine list|approve <name>", cmd_not_yet),
    "vault": ("vault add <handle>", cmd_not_yet),
    "dev": ("developer tasks: clean | sandbox-build | evals-record", cmd_dev),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mito", description="MITO operator CLI")
    parser.add_argument(
        "--version", action="version", version=f"mito {__version__} (phase {PHASE})"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command")
    for name, (help_text, _fn) in _COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        if name == "halt":
            g = p.add_mutually_exclusive_group()
            g.add_argument("--soft", action="store_true")
            g.add_argument("--hard", action="store_true")
            g.add_argument("--panic", action="store_true")
        if name == "up":
            p.add_argument(
                "--no-runtime",
                action="store_true",
                help="serve the Handbrake without starting the runtime child",
            )
        p.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    try:
        return int(_COMMANDS[ns.command][1](ns))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def repo() -> Path:  # re-exported for tests
    return repo_root()


if __name__ == "__main__":
    raise SystemExit(main())
