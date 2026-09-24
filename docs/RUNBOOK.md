# MITO — Runbook

Status: Phase 1. Filled in per phase; complete by Phase 7.

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

## Autonomy

`uv run mito autonomy set A0|A1|A2` is a signed command. DEV MODE refuses a lasting A2 (caps at
A1). A3 does not exist.

## Incident: lost operator key

Stop MITO. Delete `$MITO_HOME/control/operator.pub` and the keyring entry (or
`MITO_OPERATOR_KEY_FILE`), then `mito init` on a stopped system and `mito policy sign`. Physical
access required. (`mito init --rekey` is not implemented yet.)

## Platform notes

- Windows (PowerShell): `$env:MITO_HOME = "$HOME\.mito"`; Docker Desktop required for T1 (Phase 2).
- fish: `set -x MITO_HOME ~/.mito`
- bash: `export MITO_HOME=~/.mito`
- Control surfaces bind to `127.0.0.1` only.
