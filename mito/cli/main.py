"""MITO operator CLI (ADR-0013). Plain argparse, `--json` on every command.

Phase 7 implements: compose deploy (loopback, separate user, egress-only route).
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

PHASE = 7

NOT_YET: dict[str, str] = {}


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
        (
            f"ATP: {st.get('atp', {}).get('balance', 0):.0f}  "
            f"runway: {st.get('atp', {}).get('runway_days') or '∞'}d  "
            f"verified income: {st.get('atp', {}).get('verified_income', 0):.0f}"
        ),
        f"pending approvals: {st['pending_approvals']}   audit seq: {st['audit']['seq']}",
        f"integrity: {st['integrity']['reason'] if st['integrity'] else 'not checked yet'}",
        (
            f"docker: {st.get('docker', '?')}  egress_proxy: {st.get('egress_proxy', '?')}  "
            f"vault handles: {st.get('vault_handles', 0)}"
        ),
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


def cmd_rest(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    st = connect(_paths()).rest()
    _out(ns, st, "Deep Rest: pulse off, no model calls. `mito wake` to resume.")
    return 0


def cmd_ledger(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect, sign_command

    if not ns.args:
        snap = connect(_paths()).ledger_snapshot()
        _out(ns, snap)
        return 0
    verb = ns.args[0]
    op = connect(_paths())
    if verb == "confirm":
        if len(ns.args) < 2:
            print("usage: mito ledger confirm <id>", file=sys.stderr)
            return 2
        signed = sign_command(_paths(), "ledger.confirm", {"id": ns.args[1]})
        out = op.ledger_confirm(signed)
        _out(ns, out, f"verified claim {out.get('id')} (+{out.get('amount_atp')} ATP)")
        return 0
    if verb == "topup":
        if len(ns.args) < 2:
            print("usage: mito ledger topup <atp>", file=sys.stderr)
            return 2
        out = op.ledger_topup(float(ns.args[1]))
        _out(ns, out, f"top-up ok; balance {out.get('balance_atp')} ATP ({out.get('state')})")
        return 0
    if verb == "claims":
        snap = op.ledger_snapshot()
        _out(ns, snap.get("pending_claims", []), None)
        return 0
    print("usage: mito ledger [confirm <id>|topup <atp>|claims]", file=sys.stderr)
    return 2


def cmd_vault(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect

    if not ns.args:
        print("usage: mito vault add <handle> | list | revoke <handle>", file=sys.stderr)
        return 2
    op = connect(_paths())
    verb = ns.args[0]
    if verb == "list":
        handles = op.vault_list()
        _out(
            ns,
            handles,
            "\n".join(
                f"{h['handle']}  {'REVOKED' if h['revoked'] else 'live'}  {h.get('note') or '-'}"
                for h in handles
            )
            or "vault empty",
        )
        return 0
    if verb == "add":
        if len(ns.args) < 2:
            print("usage: mito vault add <handle>   (secret on stdin)", file=sys.stderr)
            return 2
        secret = sys.stdin.readline().rstrip("\n")
        if not secret:
            print("error: empty secret on stdin", file=sys.stderr)
            return 2
        name = op.vault_add(ns.args[1], secret)
        _out(ns, {"handle": name}, f"stored {name} (secret never printed)")
        return 0
    if verb == "revoke":
        if len(ns.args) < 2:
            print("usage: mito vault revoke <handle>", file=sys.stderr)
            return 2
        ok = op.vault_revoke(ns.args[1])
        _out(ns, {"revoked": ok}, f"revoked {ns.args[1]}" if ok else "handle not found")
        return 0 if ok else 1
    print("usage: mito vault add <handle> | list | revoke <handle>", file=sys.stderr)
    return 2


def cmd_dev(ns: argparse.Namespace) -> int:
    task = ns.args[0] if ns.args else ""
    if task == "clean":
        for d in (".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"):
            shutil.rmtree(repo_root() / d, ignore_errors=True)
        for p in repo_root().rglob("__pycache__"):
            shutil.rmtree(p, ignore_errors=True)
        print("cleaned caches")
        return 0
    if task == "sandbox-build":
        from handbrake.sandbox.runner import SandboxUnavailable, build_image

        try:
            return build_image(repo_root())
        except SandboxUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if task == "evals-record":
        print("mito dev evals-record: arrives in Phase 3", file=sys.stderr)
        return 2
    print("usage: mito dev clean|sandbox-build|evals-record", file=sys.stderr)
    return 2


def cmd_skills(ns: argparse.Namespace) -> int:
    from mito.skills_rt.frontmatter import SkillParseError
    from mito.skills_rt.loader import SkillError, approve, list_quarantine
    from mito.tools.catalog import ADVERTISED

    workspace = repo_root() / "skills"
    if not ns.args or ns.args[0] != "quarantine":
        print("usage: mito skills quarantine list | approve <name>", file=sys.stderr)
        return 2
    verb = ns.args[1] if len(ns.args) > 1 else "list"
    if verb == "list":
        items = list_quarantine(workspace, available_tools=ADVERTISED)
        _out(
            ns,
            items,
            "\n".join(
                (
                    f"{i.get('name', Path(str(i.get('path', ''))).parent.name)}  "
                    f"{'ok' if i.get('ok') else i.get('error')}"
                )
                for i in items
            )
            or "quarantine empty",
        )
        return 0
    if verb == "approve":
        if len(ns.args) < 3:
            print("usage: mito skills quarantine approve <name>", file=sys.stderr)
            return 2
        try:
            dest = approve(workspace, ns.args[2], available_tools=ADVERTISED)
        except (SkillError, SkillParseError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _out(ns, {"approved": str(dest)}, f"approved {ns.args[2]} -> {dest}")
        return 0
    print("usage: mito skills quarantine list | approve <name>", file=sys.stderr)
    return 2


def cmd_memory(ns: argparse.Namespace) -> int:
    from mito.memory.store import MemoryError, MemoryStore

    store = MemoryStore(_paths().runtime / "memory")
    if not ns.args:
        print("usage: mito memory confirm <name>", file=sys.stderr)
        return 2
    if ns.args[0] == "confirm":
        if len(ns.args) < 2:
            print("usage: mito memory confirm <name>", file=sys.stderr)
            return 2
        try:
            fact = store.confirm(ns.args[1])
        except MemoryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _out(
            ns,
            fact.to_dict(),
            f"confirmed {fact.name} (lane={fact.trust_lane})",
        )
        return 0
    print("usage: mito memory confirm <name>", file=sys.stderr)
    return 2


def cmd_dashboard(ns: argparse.Namespace) -> int:
    from mito.cli.operator import connect, handbrake_url

    ticket = connect(_paths()).dashboard_ticket()
    url = f"{handbrake_url()}/dashboard?ticket={ticket}"
    _out(ns, {"url": url}, f"open {url}  (localhost, one-time ticket)")
    return 0


def cmd_evolve(ns: argparse.Namespace) -> int:
    from mito.evolve.store import review

    if not ns.args or ns.args[0] != "review":
        print("usage: mito evolve review", file=sys.stderr)
        return 2
    rows = review(_paths().runtime / "evolve", state="THRIVING", today_count=0)
    text = "\n".join(f"{r['kind']:<6} {r['tier']} {r['path']} — {r['reason']}" for r in rows)
    _out(ns, rows, text or "inbox empty")
    return 0


def cmd_playbook(ns: argparse.Namespace) -> int:
    from handbrake.paths import repo_root

    from mito.playbooks.load import blacklist_categories, load_dir
    from mito.playbooks.runner import run_l0

    root = repo_root()
    books = load_dir(root / "playbooks", categories=blacklist_categories(root / "policy"))
    verb = ns.args[0] if ns.args else "list"
    if verb == "list":
        _out(
            ns,
            [b.to_dict() for b in books],
            "\n".join(
                f"{b.name:<20} {'on' if b.enabled else 'off':<4} {b.cron}" for b in books
            ),
        )
        return 0
    if verb == "run" and len(ns.args) >= 2:
        book = next((b for b in books if b.name == ns.args[1]), None)
        if book is None:
            print(f"unknown playbook {ns.args[1]}", file=sys.stderr)
            return 2
        out = run_l0(book, state="NORMAL", daily_burn_atp=0)
        _out(ns, out)
        return 0
    print("usage: mito playbook list | run <name>", file=sys.stderr)
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
    "rest": ("enter Deep Rest (no model calls until wake / threshold)", cmd_rest),
    "ledger": ("ledger | confirm <id> | topup <atp> | claims", cmd_ledger),
    "evolve": ("evolve review — re-judge the proposal inbox", cmd_evolve),
    "playbook": ("playbook list | run <name>  (L0, no model)", cmd_playbook),
    "skills": ("skills quarantine list|approve <name>", cmd_skills),
    "memory": ("memory confirm <name> — lift a quarantined fact", cmd_memory),
    "vault": ("vault add <handle> | list | revoke <handle>  (secret on stdin)", cmd_vault),
    "dev": ("developer tasks: clean | sandbox-build | evals-record", cmd_dev),
    "dashboard": ("print a one-time localhost dashboard URL", cmd_dashboard),
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
