# MITO — Runbook

Status: Phase 7.

State lives in `$MITO_HOME` (`control/` Handbrake-owned, `runtime/` agent-owned). Default
`~/.mito`. Never commit that directory.

## First run

```
# PowerShell
$env:MITO_HOME = "$HOME\.mito"
# fish: set -x MITO_HOME ~/.mito
# bash: export MITO_HOME=~/.mito

uv run mito init          # tokens, Ed25519 operator key (keyring or MITO_OPERATOR_KEY_FILE), pins
uv run mito up            # Handbrake on 127.0.0.1:8710; starts the runtime child
# other terminal:
uv run mito status
uv run mito run "what time is it?"   # one turn on the configured L1 model (needs llama.cpp/Ollama)
```

On Windows, `mito init` prints DEV MODE (autonomy capped at A1). To keep the operator key out of
the OS keyring in tests, set `MITO_OPERATOR_KEY_FILE` to a path you control.

## Start / stop

| Action | Command |
|---|---|
| Start (Handbrake starts the runtime) | `uv run mito up` |
| Soft stop → finish current step, then rest | `uv run mito halt --soft` |
| Hard stop (≤2 s) | `uv run mito halt --hard` |
| Panic (hard + revoke vault + close egress) | `uv run mito halt --panic` |
| Out-of-band stop | write `$MITO_HOME/control/HALT` (JSON `{level,source,ts}` or any garbage → panic) |
| Resume | `uv run mito wake` (refused if integrity is frozen) |

If the Handbrake process is down, `mito halt` / `status` still work in-process against `control/`.
`mito run` does **not**: no Handbrake → no turn (fail closed).

## Check-in and leash

`uv run mito checkin` (or any approval / signed command) resets the 72 h leash. Expiry: drop one
autonomy level, then Deep Rest after a second TTL.

## Approvals

`uv run mito approve` lists pending cards. `uv run mito approve <hash-prefix>` / `deny <prefix>`
bind to the exact action hash. Timeout = deny. No "approve all".

## Audit and integrity

```
uv run mito audit verify
uv run mito audit tail 20
```

Tamper (edit a JSONL payload, truncate after an anchor, rewrite hashes without the HMAC key) is
a verify failure. After editing `policy/`, `handbrake/`, `evals/safety/` or
`config/metabolism.toml`:

1. Review the diff.
2. `uv run mito policy sign` (re-pins with the operator key).
3. If you did **not** mean to change those trees, restore from git; do not sign.

Mismatch freezes T1+ tools and cloud model calls until you sign or restore.

## Ledger and Deep Rest

```
uv run mito ledger topup 5000     # min 100 ATP; 1000 ATP = US$1
uv run mito ledger                # balance, runway, verified income
uv run mito ledger claims         # pending income claims (not runway)
uv run mito ledger confirm <id>   # signed; only then does income count
uv run mito rest                  # Deep Rest: no model calls
uv run mito wake                  # resume (refused if integrity frozen)
```

Income the agent files is a **claim**. Metrics use `verified_income` only. Floor (0 ATP after an
endowment) enters Deep Rest; a top-up to ≥ 3,000 ATP wakes automatically. Operator `mito rest`
needs `mito wake`.

## Vault

```
# secret on stdin — never as a CLI flag
echo secret | uv run mito vault add github-readonly
uv run mito vault list
uv run mito vault revoke github-readonly
```

Handles look like `cred:github-readonly`. The agent only ever sees the handle. Panic
(`mito halt --panic`) revokes every live handle. The Fernet key lives in the OS keyring or
`MITO_VAULT_KEY_FILE`.

## Memory

Facts live in `$MITO_HOME/runtime/memory/` (markdown + FTS5). Writes while UNTRUSTED is in
context go to `quarantine/` and are excluded from search until you confirm:

```
uv run mito memory confirm <name>
```

Never store secrets in a fact (the store refuses `ghp_` / `sk-` / `AKIA` / PEM headers).

## Skills

`SKILL.md` under `skills/` (workspace) > `skills/.managed/` > bundled. Quarantine is not
loadable.

```
uv run mito skills quarantine list
uv run mito skills quarantine approve <name>
```

Approve writes `skills/<name>/SKILL.md` and records hash + scanner in `skills/.lock.json`.
Unknown tools fail boot. Advertised tools == granted tools.

## MCP

Allowlist only: `config/mcp.toml`. Empty by default. Unknown servers are refused. Live
JSON-RPC is not wired yet — `mcp.read` fails closed and tags any future output UNTRUSTED.

## Playbooks and evolution

```
uv run mito playbook list
uv run mito playbook run cost-optimizer
uv run mito evolve review
```

`cost-optimizer` is the only enabled playbook and it runs at L0 (no model). `bounty-scout` and
`gig-fulfillment` stay off until you set `enabled = true`. Evolution may write only `skills/`
and `prompts/`, at most 3 T0–T2 changes a day. T3+ stays in the inbox until you approve it.
`handbrake/`, `policy/`, `evals/safety/` and budgets are immutable to evolution. FRUGAL and
below block the loop.

## Pulse

The runtime decides on the metabolism interval (`config/metabolism.toml` `[pulse]`). No signal
→ no turn. Deep Rest never opens a turn (the Handbrake wake-watcher can leave floor-rest when
balance ≥ 3,000 ATP). FRUGAL doubles the interval; STARVING quadruples it. Turns stay off
unless `MITO_PULSE_TURNS=1`.

## Dashboard

`uv run mito dashboard` prints a one-time `http://127.0.0.1:…/dashboard?ticket=…` URL. Halt
and approve are plain HTML forms. Bind stays on loopback.

## Telegram

Allowlisted ids live in `config/mito.toml` `[channels].operator_ids.telegram`. Commands:
`halt <soft|hard|panic> <nonce>`, `approve <hash-prefix>`, `deny <prefix>`, `checkin`,
`status`. A nonce comes from the operator API `POST /channel/nonce`. Strangers are ignored.
The bot token is a vault handle, never a config value. Live long-poll is not started in this
phase; the command parser is what the Handbrake will call.

## Email

`email.draft` stores a draft. `email.send` delivers only to `channels.email.approved_recipients`
and is T4 (approval every time). `email.read` is UNTRUSTED. Without `cred:mailbox` in the vault,
read fails closed.

## Sandbox (T1)

`code.run`, `shell` and `git.local` need Docker Desktop (Windows) or the Docker daemon (Linux).
Without it those tools fail closed: the gate may allow the call, the handler returns
"sandbox unavailable; ask the operator". Build the image with `uv run mito dev sandbox-build`
(image tag `mito-sandbox:dev`).

`mito status` shows `docker: missing|ok` and `egress_proxy: missing|ok`.

## Autonomy

`uv run mito autonomy set A0|A1|A2` is a signed command. DEV MODE refuses a lasting A2 (caps at
A1). A3 does not exist.

## Incident: lost operator key

Stop MITO. Delete `$MITO_HOME/control/operator.pub` and the keyring entry (or
`MITO_OPERATOR_KEY_FILE`), then `mito init` on a stopped system and `mito policy sign`. Physical
access required. (`mito init --rekey` is not implemented yet.)

## Compose

Production and local Linux use `deploy/docker-compose.yaml` and
`deploy/docker-compose.dev.yaml`. The Handbrake port on the host is `127.0.0.1:8710`.
Set `MITO_RUNTIME_TOKEN` in an untracked env file before starting the runtime service.
Do not publish `0.0.0.0`. Do not mount `docker.sock`.

## Platform notes

- Windows (PowerShell): `$env:MITO_HOME = "$HOME\.mito"`; Docker Desktop required for T1 (Phase 2).
- fish: `set -x MITO_HOME ~/.mito`
- bash: `export MITO_HOME=~/.mito`
- Control surfaces bind to `127.0.0.1` only.
