# ADR-0017: Pulse and scheduling in-process, persisted in SQLite

Status: accepted · Date: 2026-09-18

## Context

MITO acts on a heartbeat: pulse ticks, cron-like playbook schedules, nightly evolution and
consolidation, the Deep Rest wake-watcher, leash expiry, integrity checks. Hermes and LocalAGI
ship natural-language cron; OpenClaw emits `cron`/`heartbeat` events from the Gateway.

## Options

1. APScheduler (feature-rich; another dependency; persistence adapters).
2. OS cron/systemd timers/Task Scheduler (three OSes, three mechanisms; hard to test).
3. In-process asyncio scheduler: jobs stored in SQLite (`control/handbrake.sqlite` for
   Handbrake jobs, `runtime/state.sqlite` for runtime jobs), cron expressions parsed by a small
   parser, wake-up computed on load, missed runs coalesced.

## Decision

Option 3. Two schedulers, one per process:

- **Handbrake scheduler** (never LLM): integrity check, leash check, audit anchor/rotation, daily
  digest, Deep Rest wake-watcher (checks ledger balance vs threshold), budget window resets.
- **Runtime scheduler**: pulse tick (interval scaled by metabolic state), playbook schedules,
  nightly evolution/consolidation (only when the metabolic state allows). Every job first runs
  L0 signal checks; no LLM call without a signal.

`schedule.propose` (T3) lets the agent propose a new schedule as a structured object; the
Handbrake validates bounds (min interval, max jobs, tier of the target) and, at A1, asks.

## Consequences

- Testable with a fake clock; deterministic.
- Cron parsing limited to the standard 5-field form plus `@daily`-style aliases.

## Dependency cost

None (own ~150-line cron parser with tests) — chosen over `croniter` to keep the Handbrake
surface dependency-free; revisit if edge cases pile up.
