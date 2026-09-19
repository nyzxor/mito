# ADR-0003: SQLite (WAL) for ledger, audit index and memory search

Status: accepted · Date: 2026-09-18

## Context

MITO needs: a double-entry ATP ledger with crash safety; an index over the audit chain (the
chain itself is JSONL); FTS5/BM25 search over memory; checkpoints and scheduler state. Single
operator, single host, low write volume (hundreds of rows/day).

## Options

1. SQLite in WAL mode (stdlib `sqlite3`, FTS5 available in CPython builds on all three OSes).
2. PostgreSQL (another container, another credential, overkill for one operator).
3. DuckDB (great for analytics, weaker for concurrent small writes; keep as an optional `data`
   tool inside the sandbox, not as system storage).

## Decision

Option 1. Separate database files per owner to respect the trust boundary:

- `control/ledger.sqlite`, `control/audit-index.sqlite`, `control/handbrake.sqlite`
  (approvals, leash, pins, scheduler) — Handbrake-owned, runtime read-only where needed.
- `runtime/state.sqlite` (checkpoints, session logs), `memory/index.sqlite` (FTS5) —
  runtime-owned.

Ledger is double-entry: every posting has balanced debit/credit rows; `energy:balance` is
derived, never stored as a mutable counter. Journal rows reference the audit `seq` that caused
them.

## Consequences

- Zero infra; backups are file copies (snapshots to the operator-configured destination).
- WAL requires that the runtime not hold write locks on Handbrake files — enforced by
  filesystem permissions in containers and by convention in DEV MODE.
- FTS5 must be verified at boot (`PRAGMA compile_options`); fail loudly if missing.

## Dependency cost

None (stdlib). DuckDB/pandas are sandbox-side tool dependencies, installed in the sandbox image,
not in the runtime.
