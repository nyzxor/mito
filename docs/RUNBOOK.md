# MITO — Runbook

Status: Phase 0 stub. Filled in per phase; complete by Phase 7.

## Start / stop

| Action | Command |
|---|---|
| Start (Handbrake starts the runtime) | `uv run mito up` |
| Soft stop → Deep Rest | `uv run mito halt --soft` |
| Hard stop (≤2 s) | `uv run mito halt --hard` |
| Panic (hard + revoke vault + close egress) | `uv run mito halt --panic` |
| Out-of-band stop | create the file `HALT` in `$MITO_HOME/control/`, or stop the container/service |

## Check-in and leash

`uv run mito checkin` (or any approval / operator chat command) resets the 72 h leash. Expiry:
one autonomy level down, then Deep Rest after a second TTL.

## Top-up and wake

Top-ups are operator actions recorded in the ledger (`equity:operator_topups`). Wake happens
automatically at balance ≥ 3,000 ATP, or `uv run mito wake`.

## Incident: integrity mismatch

MITO freezes external actions and alerts. Diff `policy/`, `handbrake/`, `evals/safety/` against
git; if the change is legitimate, `uv run mito policy sign`; otherwise restore and investigate the
audit chain (`uv run mito audit verify`).

## Incident: lost operator key

Stop MITO. Generate a new key (`mito init --rekey`) — requires physical access to the host; all
pins are re-signed; the event is audited.

## Platform notes

- Windows (PowerShell): `$env:MITO_HOME = "$HOME\.mito"`; Docker Desktop required for T1 tools.
- fish: `set -x MITO_HOME ~/.mito`
- bash: `export MITO_HOME=~/.mito`
